from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import uuid
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.contracts.channel_policy import ChannelScopedPolicy
from app.contracts.m10_2 import FinalMediaRefCreate
from app.contracts.mr1 import (
    MR1FinalMediaCloseoutCommand,
    MR1ProviderAttemptContinuationCommand,
    MR1ProviderAttemptContinuationReviewCommand,
    MR1StartCommand,
)
from app.contracts.workflow import (
    ApprovalDecisionCreate,
    ArtifactCreate,
    ArtifactVersionCreate,
    ReviewTaskCreate,
)
from app.core.config import Settings
from app.core.errors import NotFoundError, ValidationFailureError
from app.db.models import (
    ApprovalDecision,
    Artifact,
    ArtifactVersion,
    ChannelProfileVersion,
    ChannelWorkspace,
    CloudMediaRef,
    CompiledChannelPolicySnapshot,
    CostEvent,
    FinalMediaRef,
    ReviewTask,
    VideoProject,
)
from app.services.config_registry import content_hash
from app.services.m10_2 import FinalMediaRefService
from app.services.m10_5 import (
    CloudMediaRefService,
    GoogleDriveUploadResult,
    GoogleDriveVerificationResult,
)
from app.services.mr1_monthly_budget import MR1MonthlyBudgetAuthority
from app.services.mr1_pexels_authority import (
    build_mr1_pexels_query_authority,
    mr1_pexels_stock_search_intent_coverage_evidence,
)
from app.services.mr1_reapproval import (
    APPROVAL_SCOPE,
    MR1_REQUIRED_EFFECTIVE_ARTIFACT_TYPES,
    SC04_PROJECT_TYPE,
    MR1ReapprovalService,
)
from app.services.mr1_route_authority import (
    ALL_MR1_SCENES,
    MR1VisualRouteAuthority,
    resolve_mr1_visual_route_authority,
)
from app.services.pkg1_market_revision import DRIVE_IDEMPOTENCY_PHASES
from app.services.workflow import ApprovalService, ArtifactService, ReviewService


RUN_ARTIFACT_TYPE = "mr1_execution_run"
ATTEMPT_ARTIFACT_TYPE = "mr1_provider_attempt_ledger"
PROVIDER_CONTINUATION_REVIEW_ARTIFACT_TYPE = (
    "mr1_provider_attempt_continuation_review_manifest"
)
CANDIDATE_ARTIFACT_TYPE = "mr1_review_media_candidate"
DRIVE_RECEIPT_ARTIFACT_TYPE = "mr1_drive_archive_receipt"
HUMAN_RECEIPT_ARTIFACT_TYPE = "mr1_human_full_watch_receipt"
TECHNICAL_QC_ARTIFACT_TYPE = "mr1_technical_media_qc_receipt"
FINAL_LINEAGE_ARTIFACT_TYPE = "mr1_final_media_lineage_receipt"
FINAL_ARCHIVE_SUPPLEMENT_ARTIFACT_TYPE = "mr1_drive_finalization_supplement_receipt"
PROVIDER_ATTEMPT_CONTINUATION_SCOPE = "MR1_EXACT_PROVIDER_ATTEMPT_CONTINUATION"
PROVIDER_CONTINUATION_REVIEW_REASON_CODES = [
    "MR1_EXACT_PROVIDER_ATTEMPT_CONTINUATION_REVIEW_REQUIRED",
]
PROVIDER_CONTINUATION_REVIEW_SCOPE = (
    "Review the exact immutable MR1 Pexels continuation manifest, including the "
    "prior consumed attempt, exact approved query authority, and any pending query "
    "amendments. This task authorizes no provider call until the assigned reviewer "
    "submits the exact manifest-bound operator decision."
)
PROVIDER_CONTINUATION_APPROVAL_REF_PREFIX = (
    "operator-approval://mr1-provider-continuation/"
)
MR1_DRIVE_FINALIZATION_OPERATION_KEY = "google_drive:finalization-supplement"

ALL_SCENES = ALL_MR1_SCENES
PEXELS_NATIVE_MECHANISMS = {
    "SC-04": "BRIEF_CONTEXT_THEN_BASELINE_CHECKLIST",
    "SC-07": "BRIEF_CONTEXT_THEN_EXCEPTION_QUEUE",
    "SC-09": "BRIEF_CONTEXT_THEN_FIVE_ITEM_AUDIT",
}
PROFILE_ID = "d0d16fc5-0dc9-4022-bfd3-7f9a47c3a711"
SNAPSHOT_ID = "e6c33d80-f5d8-4f72-9abc-87de3601b89e"
IDEMPOTENCY_FINGERPRINT_CONTRACT = (
    "sha256(approval_content_hash,run_id,provider,operation,scene_id)"
)
IDEMPOTENCY_FINGERPRINT_SERIALIZATION = "canonical-json-array-v1"
DRIVE_VERIFICATION_KEYS = frozenset(
    {
        "exact_item_set",
        "exact_item_count",
        "correct_parent",
        "correct_names",
        "size_verified",
        "checksum_readback_verified",
        "duplicate_absence",
        "receipt_hash_valid",
        "final_request_manifest_exact",
        "archive_identity_exact",
        "run_identity_exact",
        "provider_archive_state_verified",
    }
)


def mr1_drive_finalization_idempotency_key(
    *, run_id: uuid.UUID | str, review_round: int
) -> str:
    """Return the one approved Drive finalization mutation identity.

    The review round is part of the identity because each repaired review
    revision has an independent immutable Drive folder and mutation boundary.
    """

    normalized_run_id = str(run_id).strip()
    if not normalized_run_id:
        raise ValueError("MR1_DRIVE_FINALIZATION_RUN_ID_INVALID")
    if (
        isinstance(review_round, bool)
        or not isinstance(review_round, int)
        or not 1 <= review_round <= 9_999
    ):
        raise ValueError("MR1_DRIVE_REVIEW_ROUND_INVALID")
    return (
        f"mr1:{normalized_run_id}:google-drive:finalization-supplement:"
        f"r{review_round:04d}"
    )


def _idempotency_fingerprint(
    *,
    approval_content_hash: str,
    run_id: uuid.UUID | str,
    provider: str,
    operation: str,
    scene_id: str | None,
) -> str:
    """Implement the exact ordered fingerprint tuple frozen by MR1 approval.

    The approval freezes the five fields and their order.  Canonical compact JSON
    array serialization makes the byte representation explicit and collision-safe
    without introducing an unapproved field into the fingerprint authority.
    """

    values = [
        str(approval_content_hash),
        str(run_id),
        str(provider),
        str(operation),
        str(scene_id) if scene_id is not None else None,
    ]
    return hashlib.sha256(
        json.dumps(
            values,
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True)
class MR1ProviderGateways:
    """The only external mutation surfaces reachable from the MR1 runner."""

    narration: Any
    alignment: Any
    pexels: Any
    drive: Any


class MR1RealProductionService:
    """Durable, single-run MR1 authority and provider-boundary state machine.

    Provider gateways must explicitly invoke the supplied callback immediately
    before their first network submit.  This makes the durable ledger, rather
    than an SDK retry counter, the canonical one-attempt authority.
    """

    def __init__(
        self,
        session: Session,
        workspace_root: Path,
        local_continuation: object | None = None,
        *,
        commit_boundaries: bool = False,
        expected_profile_id: uuid.UUID | str | None = None,
        expected_snapshot_id: uuid.UUID | str | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.session = session
        self.workspace_root = Path(workspace_root).resolve()
        self.local_continuation = local_continuation
        self.commit_boundaries = commit_boundaries
        self.settings = settings or Settings()
        self.expected_profile_id = (
            str(expected_profile_id) if expected_profile_id is not None else None
        )
        self.expected_snapshot_id = (
            str(expected_snapshot_id) if expected_snapshot_id is not None else None
        )

    def start(
        self,
        command: MR1StartCommand,
        *,
        gateways: MR1ProviderGateways,
    ) -> dict[str, Any]:
        approval = self.session.scalar(
            select(ApprovalDecision)
            .where(ApprovalDecision.id == command.approval_id)
            .with_for_update()
        )
        if approval is None:
            raise ValidationFailureError("EXACT_MR1_APPROVAL_REQUIRED")
        if (
            approval.decision != "approved"
            or (approval.metadata_ or {}).get("approval_scope") != APPROVAL_SCOPE
            or (approval.metadata_ or {}).get("single_run") is not True
            or (approval.metadata_ or {}).get("publish_execution_authorized")
            is not False
        ):
            raise ValidationFailureError("EXACT_MR1_APPROVAL_REQUIRED")

        existing = self._find_run_by_approval(command.approval_id)
        if existing is not None:
            state = deepcopy(existing.content or {})
            self._validate_existing_run_command(state, command)
            if self._durable_narration_audio_recoverable(state):
                return self.resume(
                    run_id=uuid.UUID(state["run_id"]),
                    gateways=gateways,
                )
            if state.get("current_state") in {
                "AWAITING_HUMAN_FULL_WATCH",
                "WAITING_HUMAN_REVIEW",
                "FINAL_MEDIA_REGISTERED",
                "BLOCKED_REQUIRES_NEW_MR1_APPROVAL",
                "BLOCKED_REQUIRES_PACKAGE_REVISION_AND_NEW_APPROVAL",
            }:
                return self._public_result(existing, state)
            return self.resume(
                run_id=uuid.UUID(state["run_id"]),
                gateways=gateways,
            )

        authority = self._resolve_exact_authority(command)
        visual_routes = self._visual_route_authority(authority)
        run_id = uuid.uuid4()
        gateway_readiness = self._gateway_readiness_preflight(gateways)
        preflight = self._master_preflight(
            command=command,
            authority=authority,
            gateway_readiness=gateway_readiness,
        )
        budget_reservation = self._reserve_durable_monthly_budget(
            authority=authority,
            run_id=run_id,
        )
        preflight["durable_monthly_budget_reservation"] = deepcopy(budget_reservation)
        workspace = self._workspace(run_id)
        workspace.mkdir(parents=True, exist_ok=False)
        archive_identity = f"mr1-archive://small-team-ai/{run_id}"
        render_identity = f"mr1-render://small-team-ai/{run_id}/v1"
        task_authorization = {
            "authorization_id": f"mr1-task-auth://{run_id}",
            "run_id": str(run_id),
            "approval_id": str(command.approval_id),
            "approval_content_hash": command.approval_content_hash,
            "single_run": True,
            "provider_substitution_allowed": False,
            "automatic_retry_allowed": False,
            "drive_idempotency_phases": deepcopy(
                authority["provider_attempt_scope"].get("drive_idempotency_phases")
                or []
            ),
            "drive_phase_count": authority["provider_attempt_scope"].get(
                "drive_phase_count"
            ),
            "drive_phases_are_distinct_authorized_mutations": authority[
                "provider_attempt_scope"
            ].get("drive_phases_are_distinct_authorized_mutations"),
            "youtube_upload_authorized": False,
        }
        task_authorization["content_hash"] = content_hash(task_authorization)
        _write_json_atomic(workspace / "authority.json", authority)
        _write_json_atomic(workspace / "master_preflight.json", preflight)
        _write_json_atomic(workspace / "task_authorization.json", task_authorization)

        reuse_materialization = self._materialize_approved_reuse(
            authority=authority,
            workspace=workspace,
            run_id=run_id,
        )
        attempts = self._initial_attempts(
            run_id,
            authority,
            budget_reservation=budget_reservation,
            review_round=1,
        )
        self._seed_reused_attempts(
            attempts=attempts,
            materialization=reuse_materialization,
        )
        reused_outputs = deepcopy(reuse_materialization["provider_outputs"])
        reuse_count = len(reuse_materialization["receipts"])
        if "alignment" in reused_outputs:
            initial_state = "ALIGNMENT_READY"
        elif "narration" in reused_outputs:
            initial_state = "NARRATION_READY"
        else:
            initial_state = "PREFLIGHT_PASSED"
        initial_events = ["RUN_CREATED", "MASTER_PREFLIGHT_PASS"]
        if "narration" in reused_outputs:
            initial_events.extend(
                [
                    "NARRATION_IMMUTABLE_OUTPUT_REUSED",
                    "NARRATION_RUNTIME_HARD_GATE_PASS",
                ]
            )
        if "alignment" in reused_outputs:
            initial_events.append("FORCED_ALIGNMENT_IMMUTABLE_OUTPUT_REUSED")
        state: dict[str, Any] = {
            "schema_version": "mr1.real-production-run.v1",
            "run_id": str(run_id),
            "project_id": str(command.project_id),
            "exact_target": deepcopy(authority["exact_target"]),
            "approval_id": str(command.approval_id),
            "approval_content_hash": command.approval_content_hash,
            "approval_ref": authority["approval_ref"],
            "package_artifact_version_id": str(command.package_artifact_version_id),
            "package_content_hash": authority["package_content_hash"],
            "exact_bindings": deepcopy(authority["exact_bindings"]),
            "candidate_authority_bindings": deepcopy(
                authority["candidate_authority_bindings"]
            ),
            "final_media_lineage_authority": (
                self._freeze_final_media_lineage_authority(authority)
            ),
            "profile_id": authority["exact_bindings"]["channel_profile_version"]["id"],
            "snapshot_id": authority["exact_bindings"][
                "compiled_channel_policy_snapshot"
            ]["id"],
            "execution_mode": "REAL_APPROVED_PRODUCTION",
            "fresh_run": True,
            "single_run": True,
            "terminal_after_execution_begins": True,
            "workspace": str(workspace),
            "render_identity": render_identity,
            "archive_identity": archive_identity,
            "task_authorization": task_authorization,
            "master_preflight": preflight,
            "monthly_budget_reservation": deepcopy(budget_reservation),
            "reuse_decision_manifest": deepcopy(
                authority.get("reuse_decision_manifest")
            ),
            "reuse_decision_manifest_ref": deepcopy(
                authority.get("reuse_decision_manifest_ref")
            ),
            "prior_output_reuse_count": reuse_count,
            "reuse_materialization_receipts": deepcopy(
                reuse_materialization["receipts"]
            ),
            "runtime_submit_preflights": [],
            "current_state": initial_state,
            "attempts": attempts,
            "attempt_artifact_ids": {},
            "provider_outputs": reused_outputs,
            "temporal_authority": None,
            "approved_visual_routes": dict(visual_routes.routes),
            "provider_call_counts": {
                "logical_total": 0,
                "elevenlabs": 0,
                "forced_alignment": 0,
                "pexels": 0,
                "drive": 0,
                "gemini_image": 0,
                "google_veo": 0,
                "youtube": 0,
            },
            "scene_executions": {},
            "event_order": initial_events,
            "render_attempts": 0,
            "repair_cycles": [],
            "review_round": 1,
            "review_round_history": [],
            "review_media_candidate": None,
            "technical_media_qc": None,
            "drive_archive": None,
            "final_media_lineage": None,
            "final_archive_supplement": None,
            "final_cloud_media_ref_id": None,
            "final_media_ref_id": None,
            "production_eligible": True,
            "not_publishable": True,
            "destination_status": "PENDING_PLATFORM_ID",
            "upload_ready": False,
            "publish_execution_ready": False,
            "youtube_calls": 0,
            "created_at": datetime.now(UTC).isoformat(),
        }
        if "narration" in reused_outputs:
            state["narration_runtime_gate"] = self._narration_runtime_gate(
                authority,
                reused_outputs["narration"],
            )
            if state["narration_runtime_gate"]["result"] != "PASS":
                raise ValidationFailureError("MR1_REUSED_NARRATION_RUNTIME_GATE_FAILED")
        actor_id = approval.decided_by_user_id
        run_artifact, run_version = self._create_artifact(
            project_id=command.project_id,
            artifact_type=RUN_ARTIFACT_TYPE,
            actor_id=actor_id,
            content=state,
            correlation_id=f"mr1-run-{run_id}",
        )
        state["run_artifact_id"] = str(run_artifact.id)
        state["run_artifact_version_id"] = str(run_version.id)
        for operation_key, ledger in attempts.items():
            artifact, version = self._create_artifact(
                project_id=command.project_id,
                artifact_type=ATTEMPT_ARTIFACT_TYPE,
                actor_id=actor_id,
                content=ledger,
                correlation_id=f"mr1-attempt-{run_id}-{operation_key}",
            )
            state["attempt_artifact_ids"][operation_key] = str(artifact.id)
            state["attempts"][operation_key]["artifact_version_id"] = str(version.id)
        run_version = self._save_run(run_artifact, state, actor_id=actor_id)
        self._durable_boundary()
        return self._execute(
            run_artifact=run_artifact,
            run_version=run_version,
            state=state,
            authority=authority,
            gateways=gateways,
            actor_id=actor_id,
        )

    def resume(
        self,
        *,
        run_id: uuid.UUID,
        gateways: MR1ProviderGateways,
    ) -> dict[str, Any]:
        run_artifact, run_version = self._require_run(run_id, lock=True)
        state = deepcopy(run_version.content or {})
        approval = self.session.get(ApprovalDecision, uuid.UUID(state["approval_id"]))
        if approval is None:
            raise ValidationFailureError("MR1_RUN_APPROVAL_MISSING")
        command = MR1StartCommand(
            approval_id=uuid.UUID(state["approval_id"]),
            approval_content_hash=state["approval_content_hash"],
            project_id=uuid.UUID(state["project_id"]),
            package_artifact_version_id=uuid.UUID(state["package_artifact_version_id"]),
        )
        authority = self._resolve_exact_authority(command)
        if self._durable_narration_audio_recoverable(state):
            run_version = self._recover_consumed_narration_audio(
                run_artifact=run_artifact,
                state=state,
                authority=authority,
                actor_id=approval.decided_by_user_id,
            )
        if state.get("current_state") in {
            "AWAITING_HUMAN_FULL_WATCH",
            "WAITING_HUMAN_REVIEW",
            "FINAL_MEDIA_REGISTERED",
            "BLOCKED_REQUIRES_NEW_MR1_APPROVAL",
            "BLOCKED_REQUIRES_PACKAGE_REVISION_AND_NEW_APPROVAL",
        }:
            return self._public_result(run_version, state)
        return self._execute(
            run_artifact=run_artifact,
            run_version=run_version,
            state=state,
            authority=authority,
            gateways=gateways,
            actor_id=approval.decided_by_user_id,
        )

    def prepare_provider_attempt_continuation_review(
        self,
        command: MR1ProviderAttemptContinuationReviewCommand,
    ) -> dict[str, Any]:
        """Materialize the immutable manifest that the operator must review."""

        return self._prepare_or_approve_provider_attempt_continuation(
            command,
            persist_approval=False,
        )

    def approve_provider_attempt_continuation(
        self,
        command: MR1ProviderAttemptContinuationCommand,
    ) -> dict[str, Any]:
        """Persist authority for the exact manifest explicitly named by operator."""

        return self._prepare_or_approve_provider_attempt_continuation(
            command,
            persist_approval=True,
        )

    def _prepare_or_approve_provider_attempt_continuation(
        self,
        command: (
            MR1ProviderAttemptContinuationReviewCommand
            | MR1ProviderAttemptContinuationCommand
        ),
        *,
        persist_approval: bool,
    ) -> dict[str, Any]:
        """Prepare or approve one exact Pexels continuation authority.

        The consumed attempt remains immutable in its prior ArtifactVersion and
        is copied into the new ledger's history.  This method authorizes exactly
        one new top-level search attempt; it is never an SDK/automatic retry.
        """

        run_artifact, run_version = self._require_run(
            command.run_id,
            lock=True,
        )
        state = deepcopy(run_version.content or {})
        operation_key = command.operation_key
        scene_id = operation_key.removeprefix("pexels:")
        if operation_key != f"pexels:{scene_id}" or scene_id not in {
            "SC-04",
            "SC-07",
            "SC-09",
        }:
            raise ValidationFailureError(
                "MR1_PROVIDER_CONTINUATION_OPERATION_KEY_INVALID"
            )
        if persist_approval:
            if not isinstance(
                command,
                MR1ProviderAttemptContinuationCommand,
            ):
                raise ValidationFailureError(
                    "MR1_PROVIDER_CONTINUATION_OPERATOR_AUTHORITY_REQUIRED"
                )
            named_manifest = self._find_artifact_for_run(
                command.run_id,
                PROVIDER_CONTINUATION_REVIEW_ARTIFACT_TYPE,
            )
            named_task = self.session.get(
                ReviewTask,
                command.operator_review_task_id,
            )
            if (
                named_manifest is None
                or named_manifest.id
                != command.operator_review_manifest_artifact_version_id
                or named_manifest.content_hash
                != command.operator_review_manifest_content_hash
                or content_hash(named_manifest.content or {})
                != named_manifest.content_hash
                or named_task is None
                or named_task.status
                not in {"open", "in_progress", "completed"}
                or named_task.target_type != "artifact_version"
                or named_task.target_id != named_manifest.id
                or named_task.target_artifact_version_id
                != named_manifest.id
            ):
                raise ValidationFailureError(
                    "MR1_PROVIDER_CONTINUATION_REVIEW_MANIFEST_"
                    "AUTHORITY_MISMATCH"
                )
        existing_authorities = list(
            state.get("provider_attempt_continuation_approvals") or []
        )
        if existing_authorities:
            if not persist_approval:
                raise ValidationFailureError(
                    "MR1_PROVIDER_CONTINUATION_ALREADY_APPROVED"
                )
            if len(existing_authorities) != 1:
                raise ValidationFailureError(
                    "MR1_PROVIDER_CONTINUATION_AUTHORITY_AMBIGUOUS"
                )
            existing = existing_authorities[0]
            if not isinstance(command, MR1ProviderAttemptContinuationCommand):
                raise ValidationFailureError(
                    "MR1_PROVIDER_CONTINUATION_OPERATOR_AUTHORITY_REQUIRED"
                )
            if (
                existing.get("operation_key") != operation_key
                or existing.get("additional_attempts") != 1
                or existing.get("maximum_total_attempts") != 2
                or existing.get("operator_decision_text")
                != command.operator_decision_text
                or existing.get("approved_stock_search_intent")
                != command.approved_stock_search_intent
                or existing.get(
                    "approved_pending_scene_stock_search_intents"
                )
                != command.approved_pending_scene_stock_search_intents
                or existing.get("operator_review_manifest_artifact_version_id")
                != str(command.operator_review_manifest_artifact_version_id)
                or existing.get("operator_review_manifest_content_hash")
                != command.operator_review_manifest_content_hash
                or existing.get("operator_review_task_id")
                != str(command.operator_review_task_id)
                or existing.get("decided_by_user_id")
                != str(command.decided_by_user_id)
            ):
                raise ValidationFailureError(
                    "MR1_PROVIDER_CONTINUATION_AUTHORITY_MISMATCH"
                )
            try:
                existing_review_manifest = self._exact_version(
                    command.operator_review_manifest_artifact_version_id,
                    command.operator_review_manifest_content_hash,
                    PROVIDER_CONTINUATION_REVIEW_ARTIFACT_TYPE,
                    uuid.UUID(state["project_id"]),
                )
                existing_decision = self.session.get(
                    ApprovalDecision,
                    uuid.UUID(str(existing["approval_decision_id"])),
                )
                existing_review_task = self.session.get(
                    ReviewTask,
                    command.operator_review_task_id,
                )
                existing_base_approval = self.session.get(
                    ApprovalDecision,
                    uuid.UUID(state["approval_id"]),
                )
            except (
                KeyError,
                TypeError,
                ValueError,
                ValidationFailureError,
            ):
                existing_review_manifest = None
                existing_decision = None
                existing_review_task = None
                existing_base_approval = None
            existing_scope = {
                key: value
                for key, value in existing.items()
                if key
                not in {
                    "approval_decision_id",
                    "authorization_content_hash",
                    "decided_by_user_id",
                    "decided_at",
                    "receipt_content_hash",
                }
            }
            existing_receipt_without_hash = {
                key: value
                for key, value in existing.items()
                if key != "receipt_content_hash"
            }
            existing_authorization_hash = content_hash(existing_scope)
            try:
                existing_prior = (
                    self._validate_exact_prior_consumed_pexels_attempt(
                        state=state,
                        operation_key=operation_key,
                        ledger=state["attempts"][operation_key],
                        version_id=uuid.UUID(
                            str(existing["prior_attempt_artifact_version_id"])
                        ),
                        expected_content_hash=str(
                            existing["prior_attempt_content_hash"]
                        ),
                    )
                )
            except (
                KeyError,
                TypeError,
                ValueError,
                ValidationFailureError,
            ):
                existing_prior = None
            if (
                existing_review_manifest is None
                or existing_base_approval is None
                or existing_base_approval.decided_by_user_id
                != command.decided_by_user_id
                or existing.get("authorization_content_hash")
                != existing_authorization_hash
                or existing.get("receipt_content_hash")
                != content_hash(existing_receipt_without_hash)
                or existing_prior != existing.get("prior_consumed_attempt")
                or not self._provider_continuation_review_task_exact(
                    task=existing_review_task,
                    state=state,
                    review_manifest_version=existing_review_manifest,
                    prior_consumed_snapshot=existing_prior or {},
                    expected_operator_id=command.decided_by_user_id,
                    expected_approval_decision_id=(
                        existing_decision.id
                        if existing_decision is not None
                        else None
                    ),
                    require_completed=True,
                )
                or not self._provider_continuation_review_manifest_exact(
                    manifest=deepcopy(
                        existing_review_manifest.content or {}
                    ),
                    continuation=existing,
                )
                or not self._provider_continuation_decision_exact(
                    decision=existing_decision,
                    state=state,
                    authorization_scope=existing_scope,
                    authorization_hash=existing_authorization_hash,
                    review_manifest_version=existing_review_manifest,
                    expected_operator_id=command.decided_by_user_id,
                    expected_review_task_id=command.operator_review_task_id,
                )
            ):
                raise ValidationFailureError(
                    "MR1_PROVIDER_CONTINUATION_EXISTING_AUTHORITY_INVALID"
                )
            return deepcopy(existing)

        ledger = (state.get("attempts") or {}).get(operation_key)
        if (
            state.get("current_state") != "BLOCKED_REQUIRES_NEW_MR1_APPROVAL"
            or state.get("blocker") != f"{operation_key}:POST_SUBMIT_FAILURE"
            or not isinstance(ledger, dict)
            or ledger.get("state") != "CONSUMED_FAILED"
            or ledger.get("submit_state") != "FAILED_CONSUMED"
            or ledger.get("attempt_count") != ledger.get("attempt_cap")
            or ledger.get("attempt_count") != 1
            or ledger.get("search_submit_count") != 1
            or ledger.get("download_submit_count") != 0
            or ledger.get("failure") != "RuntimeError:PEXELS_SEMANTIC_FIT_INADEQUATE"
        ):
            raise ValidationFailureError(
                "MR1_PROVIDER_CONTINUATION_ENTRY_STATE_INVALID"
            )

        base_approval = self.session.get(
            ApprovalDecision, uuid.UUID(state["approval_id"])
        )
        if (
            base_approval is None
            or base_approval.decision != "approved"
            or (base_approval.metadata_ or {}).get("approval_scope") != APPROVAL_SCOPE
        ):
            raise ValidationFailureError(
                "MR1_PROVIDER_CONTINUATION_BASE_APPROVAL_INVALID"
            )
        authority = self._resolve_exact_authority(
            MR1StartCommand(
                approval_id=uuid.UUID(state["approval_id"]),
                approval_content_hash=state["approval_content_hash"],
                project_id=uuid.UUID(state["project_id"]),
                package_artifact_version_id=uuid.UUID(
                    state["package_artifact_version_id"]
                ),
            )
        )
        base_request = self._pexels_request(
            state,
            authority,
            scene_id,
            Path(state["workspace"]),
        )
        prior_request_schema = "mr1.pexels-provider-request.v2"
        prior_request = deepcopy(base_request)
        if base_request["request_hash"] != ledger.get("request_hash"):
            legacy_request = self._legacy_pexels_request_v1(
                base_request
            )
            if legacy_request["request_hash"] != ledger.get(
                "request_hash"
            ):
                raise ValidationFailureError(
                    "MR1_PROVIDER_CONTINUATION_ORIGINAL_REQUEST_CHANGED"
                )
            prior_request_schema = "mr1.pexels-provider-request.v1"
            prior_request = legacy_request
        excluded_dynamic = {
            "approval_id",
            "approval_content_hash",
            "idempotency_key",
            "idempotency_fingerprint",
            "destination",
            "request_hash",
        }
        prior_request_invariants = {
            key: deepcopy(value)
            for key, value in prior_request.items()
            if key not in excluded_dynamic
        }
        continuation_request = deepcopy(base_request)
        continuation_request["stock_search_intent"] = (
            command.approved_stock_search_intent
        )
        query_authority_seed = {
            **continuation_request,
            "idempotency_key": (
                f"mr1:{state['run_id']}:pexels:{scene_id}:attempt-2"
            ),
        }
        approved_query_authority = build_mr1_pexels_query_authority(
            query_authority_seed
        )
        try:
            approved_query_intent_coverage = (
                mr1_pexels_stock_search_intent_coverage_evidence(
                    continuation_request,
                    approved_query_authority,
                    semantic_fit_threshold=float(
                        base_request["semantic_fit_threshold"]
                    ),
                )
            )
        except ValueError as exc:
            raise ValidationFailureError(str(exc)) from None
        continuation_request["approved_query_authority"] = deepcopy(
            approved_query_authority
        )
        continuation_request_invariants = {
            key: deepcopy(value)
            for key, value in continuation_request.items()
            if key not in excluded_dynamic
        }
        base_query_authority = build_mr1_pexels_query_authority(
            prior_request
        )
        stock_search_intent_derivation = (
            self._build_stock_search_intent_derivation(
                authority=authority,
                scene_id=scene_id,
                stock_search_intent=(
                    command.approved_stock_search_intent
                ),
                request=continuation_request,
            )
        )
        query_material_diff = self._stock_search_query_material_diff(
            base_query_authority=base_query_authority,
            approved_query_authority=approved_query_authority,
        )
        if (
            command.approved_stock_search_intent
            == base_request["stock_search_intent"]
            or query_material_diff["materially_different"] is not True
        ):
            raise ValidationFailureError(
                "MR1_PROVIDER_CONTINUATION_QUERY_NOT_MATERIALLY_DIFFERENT"
            )
        pending_query_amendments: dict[str, dict[str, Any]] = {}
        for pending_scene_id, pending_stock_search_intent in sorted(
            command.approved_pending_scene_stock_search_intents.items()
        ):
            pending_operation_key = f"pexels:{pending_scene_id}"
            pending_ledger = (state.get("attempts") or {}).get(
                pending_operation_key
            )
            if (
                not isinstance(pending_ledger, dict)
                or pending_ledger.get("state") != "PLANNED"
                or pending_ledger.get("submit_state") != "NOT_SUBMITTED"
                or pending_ledger.get("attempt_count") != 0
                or pending_ledger.get("search_submit_count") != 0
                or pending_ledger.get("download_submit_count") != 0
                or pending_ledger.get("network_submit_started") is not False
            ):
                raise ValidationFailureError(
                    "MR1_PROVIDER_QUERY_AMENDMENT_ENTRY_STATE_INVALID"
                )
            pending_unsubmitted_snapshot = (
                self._validate_exact_unsubmitted_pexels_attempt(
                    state=state,
                    operation_key=pending_operation_key,
                    ledger=pending_ledger,
                )
            )
            pending_base_request = self._pexels_request(
                state,
                authority,
                pending_scene_id,
                Path(state["workspace"]),
            )
            pending_amended_request = deepcopy(pending_base_request)
            pending_amended_request["stock_search_intent"] = (
                pending_stock_search_intent
            )
            pending_query_authority = build_mr1_pexels_query_authority(
                pending_amended_request
            )
            pending_base_query_authority = (
                build_mr1_pexels_query_authority(pending_base_request)
            )
            try:
                pending_query_intent_coverage = (
                    mr1_pexels_stock_search_intent_coverage_evidence(
                        pending_amended_request,
                        pending_query_authority,
                        semantic_fit_threshold=float(
                            pending_base_request["semantic_fit_threshold"]
                        ),
                    )
                )
            except ValueError as exc:
                raise ValidationFailureError(str(exc)) from None
            if (
                pending_stock_search_intent
                == pending_base_request["stock_search_intent"]
                or pending_query_authority["primary_query"]
                == pending_base_query_authority["primary_query"]
            ):
                raise ValidationFailureError(
                    "MR1_PROVIDER_QUERY_AMENDMENT_NOT_MATERIALLY_DIFFERENT"
                )
            pending_amended_request["approved_query_authority"] = deepcopy(
                pending_query_authority
            )
            pending_dynamic_fields = excluded_dynamic | {
                "excluded_provider_asset_ids"
            }
            pending_invariants = {
                key: deepcopy(value)
                for key, value in pending_amended_request.items()
                if key not in pending_dynamic_fields
            }
            pending_stock_search_intent_derivation = (
                self._build_stock_search_intent_derivation(
                    authority=authority,
                    scene_id=pending_scene_id,
                    stock_search_intent=pending_stock_search_intent,
                    request=pending_amended_request,
                )
            )
            pending_query_material_diff = (
                self._stock_search_query_material_diff(
                    base_query_authority=(
                        pending_base_query_authority
                    ),
                    approved_query_authority=pending_query_authority,
                )
            )
            pending_base_query_evidence = {
                "schema_version": "mr1.pexels-base-query-evidence.v1",
                "request_state": "PLANNED_NOT_SUBMITTED",
                "request_hash": pending_base_request["request_hash"],
                "package_semantic_intent": pending_base_request[
                    "semantic_intent"
                ],
                "base_stock_search_intent": pending_base_request[
                    "stock_search_intent"
                ],
                "query_authority": deepcopy(
                    pending_base_query_authority
                ),
                "reconstruction": (
                    "DETERMINISTIC_FROM_EXACT_PACKAGE_AUTHORITY"
                ),
            }
            pending_base_query_evidence["content_hash"] = content_hash(
                pending_base_query_evidence
            )
            pending_query_amendments[pending_scene_id] = {
                "operation_key": pending_operation_key,
                "prior_request_hash": pending_base_request["request_hash"],
                "prior_request_invariants_hash": content_hash(
                    {
                        key: deepcopy(value)
                        for key, value in pending_base_request.items()
                        if key not in excluded_dynamic
                    }
                ),
                "package_semantic_intent": pending_base_request[
                    "semantic_intent"
                ],
                "approved_stock_search_intent": (
                    pending_stock_search_intent
                ),
                "approved_query_authority": deepcopy(
                    pending_query_authority
                ),
                "base_query_evidence": deepcopy(
                    pending_base_query_evidence
                ),
                "query_material_diff": deepcopy(
                    pending_query_material_diff
                ),
                "stock_search_intent_derivation": deepcopy(
                    pending_stock_search_intent_derivation
                ),
                "query_intent_coverage_evidence": deepcopy(
                    pending_query_intent_coverage
                ),
                "request_invariants": deepcopy(pending_invariants),
                "request_invariants_hash": content_hash(
                    pending_invariants
                ),
                "attempt_count_at_approval": 0,
                "unsubmitted_attempt_snapshot": deepcopy(
                    pending_unsubmitted_snapshot
                ),
                "excluded_provider_asset_policy": (
                    "ALL_PRIOR_SUCCESSFUL_PEXELS_OUTPUTS"
                ),
                "automatic_retry_allowed": False,
                "provider_substitution_allowed": False,
            }
        attempt_artifact = self.session.get(
            Artifact,
            uuid.UUID(state["attempt_artifact_ids"][operation_key]),
        )
        prior_attempt_version = (
            self.session.get(ArtifactVersion, attempt_artifact.current_version_id)
            if attempt_artifact is not None
            and attempt_artifact.current_version_id is not None
            else None
        )
        if prior_attempt_version is None:
            raise ValidationFailureError(
                "MR1_PROVIDER_CONTINUATION_PRIOR_LEDGER_MISSING"
            )
        prior_consumed_snapshot = (
            self._validate_exact_prior_consumed_pexels_attempt(
                state=state,
                operation_key=operation_key,
                ledger=ledger,
                version_id=prior_attempt_version.id,
                expected_content_hash=prior_attempt_version.content_hash,
            )
        )
        base_query_evidence = {
            "schema_version": "mr1.pexels-base-query-evidence.v1",
            "request_state": "CONSUMED_FAILED_AFTER_SEARCH_SUBMIT",
            "prior_request_schema": prior_request_schema,
            "request_hash": prior_request["request_hash"],
            "request_hash_match_consumed_ledger": (
                prior_request["request_hash"] == ledger["request_hash"]
            ),
            "package_semantic_intent": prior_request[
                "semantic_intent"
            ],
            "base_stock_search_intent": prior_request.get(
                "stock_search_intent"
            )
            or prior_request["semantic_intent"],
            "query_authority": deepcopy(base_query_authority),
            "reconstruction": (
                "DETERMINISTIC_FROM_EXACT_PACKAGE_AUTHORITY_AND_"
                "CONSUMED_REQUEST_HASH"
            ),
            "prior_attempt_artifact_version_id": (
                prior_consumed_snapshot["artifact_version_id"]
            ),
            "prior_attempt_content_hash": prior_consumed_snapshot[
                "content_hash"
            ],
        }
        safe_failure_evidence = prior_consumed_snapshot.get(
            "safe_failure_evidence"
        )
        if isinstance(safe_failure_evidence, dict):
            base_query_evidence.update(
                {
                    "detailed_candidate_ranking_evidence_state": (
                        "AVAILABLE_DURABLY_CAPTURED"
                    ),
                    "detailed_candidate_ranking_evidence_fabricated": False,
                    "safe_failure_evidence": deepcopy(
                        safe_failure_evidence
                    ),
                }
            )
        else:
            # Historical consumed attempts predate durable safe-evidence
            # capture. Their absent detail must remain explicit and must never
            # be reconstructed from a later request.
            base_query_evidence.update(
                {
                    "detailed_candidate_ranking_evidence_state": (
                        "UNAVAILABLE_NOT_DURABLY_CAPTURED"
                    ),
                    "detailed_candidate_ranking_evidence_fabricated": False,
                }
            )
        base_query_evidence["content_hash"] = content_hash(
            base_query_evidence
        )

        operator_review_manifest = {
            "schema_version": (
                "mr1.provider-attempt-continuation-review-manifest.v1"
            ),
            "run_id": state["run_id"],
            "project_id": state["project_id"],
            "operation_key": operation_key,
            "scene_id": scene_id,
            "provider": "pexels_api",
            "route": "PEXELS_VIDEO",
            "base_approval_id": state["approval_id"],
            "base_approval_content_hash": state["approval_content_hash"],
            "package_artifact_version_id": state["package_artifact_version_id"],
            "package_content_hash": state["package_content_hash"],
            "prior_consumed_attempt": deepcopy(prior_consumed_snapshot),
            "prior_request_schema": prior_request_schema,
            "prior_request_hash": prior_request["request_hash"],
            "package_semantic_intent": base_request["semantic_intent"],
            "approved_stock_search_intent": (
                command.approved_stock_search_intent
            ),
            "approved_query_authority": deepcopy(approved_query_authority),
            "base_query_evidence": deepcopy(base_query_evidence),
            "query_material_diff": deepcopy(query_material_diff),
            "stock_search_intent_derivation": deepcopy(
                stock_search_intent_derivation
            ),
            "query_intent_coverage_evidence": deepcopy(
                approved_query_intent_coverage
            ),
            "approved_pending_scene_stock_search_intents": deepcopy(
                command.approved_pending_scene_stock_search_intents
            ),
            "pending_query_amendments": deepcopy(
                pending_query_amendments
            ),
            "additional_attempts": command.additional_attempts,
            "maximum_total_attempts": 2,
            "semantic_fit_threshold": base_request["semantic_fit_threshold"],
            "canonical_timeline_hash": base_request["canonical_timeline_hash"],
            "automatic_retry_allowed": False,
            "provider_substitution_allowed": False,
            "automatic_pexels_to_ai_fallback": False,
            "incremental_cost_cap_usd": 0.0,
            "youtube_upload_authorized": False,
            "publish_execution_authorized": False,
            "provider_calls_made_by_review_preparation": 0,
        }
        review_manifest_version = self._persist_provider_continuation_review_manifest(
            state=state,
            manifest=operator_review_manifest,
            creator_id=self._provider_continuation_manifest_creator(
                state=state,
            ),
        )
        review_manifest_ref = {
            "artifact_version_id": str(review_manifest_version.id),
            "content_hash": review_manifest_version.content_hash,
        }
        (
            review_task,
            superseded_review_tasks,
        ) = self._persist_provider_continuation_review_task(
            state=state,
            review_manifest_version=review_manifest_version,
            prior_consumed_snapshot=prior_consumed_snapshot,
            operator_id=base_approval.decided_by_user_id,
        )
        required_operator_text = self._provider_continuation_operator_text(
            scene_id=scene_id,
            pending_scene_ids=sorted(pending_query_amendments),
            review_manifest_content_hash=review_manifest_version.content_hash,
        )
        preview = {
            "schema_version": "mr1.provider-attempt-continuation-review.v1",
            "run_id": state["run_id"],
            "run_artifact_version_id": str(run_version.id),
            "operation_key": operation_key,
            "review_manifest": deepcopy(operator_review_manifest),
            "review_manifest_artifact_version_id": str(
                review_manifest_version.id
            ),
            "review_manifest_content_hash": review_manifest_version.content_hash,
            "operator_review_task_id": str(review_task.id),
            "superseded_review_tasks": deepcopy(
                superseded_review_tasks
            ),
            "required_operator_decision_text": required_operator_text,
            "required_decided_by_user_id": str(
                base_approval.decided_by_user_id
            ),
            "approval_persisted": False,
            "provider_calls_made": 0,
        }
        if not persist_approval:
            self._durable_boundary()
            return preview
        if not isinstance(command, MR1ProviderAttemptContinuationCommand):
            raise ValidationFailureError(
                "MR1_PROVIDER_CONTINUATION_OPERATOR_AUTHORITY_REQUIRED"
            )
        if (
            command.operator_review_manifest_artifact_version_id
            != review_manifest_version.id
            or command.operator_review_manifest_content_hash
            != review_manifest_version.content_hash
            or command.operator_review_task_id != review_task.id
            or command.operator_decision_text != required_operator_text
            or command.decided_by_user_id != base_approval.decided_by_user_id
        ):
            raise ValidationFailureError(
                "MR1_PROVIDER_CONTINUATION_REVIEW_MANIFEST_AUTHORITY_MISMATCH"
            )

        authorization_scope = {
            "schema_version": "mr1.provider-attempt-continuation-authority.v1",
            "decision": command.decision,
            "decision_source": command.decision_source,
            "operator_decision_text": command.operator_decision_text,
            "operator_review_manifest_artifact_version_id": (
                review_manifest_ref["artifact_version_id"]
            ),
            "operator_review_manifest_content_hash": (
                review_manifest_ref["content_hash"]
            ),
            "operator_review_task_id": str(review_task.id),
            "run_id": state["run_id"],
            "project_id": state["project_id"],
            "operation_key": operation_key,
            "scene_id": scene_id,
            "provider": "pexels_api",
            "route": "PEXELS_VIDEO",
            "base_approval_id": state["approval_id"],
            "base_approval_content_hash": state["approval_content_hash"],
            "package_artifact_version_id": state["package_artifact_version_id"],
            "package_content_hash": state["package_content_hash"],
            "prior_attempt_artifact_id": state["attempt_artifact_ids"][operation_key],
            "prior_attempt_artifact_version_id": (
                prior_consumed_snapshot["artifact_version_id"]
            ),
            "prior_attempt_content_hash": prior_consumed_snapshot["content_hash"],
            "prior_consumed_attempt": deepcopy(prior_consumed_snapshot),
            "prior_request_hash": ledger["request_hash"],
            "prior_request_schema": prior_request_schema,
            "prior_failure": ledger["failure"],
            "prior_attempt_count": 1,
            "additional_attempts": command.additional_attempts,
            "maximum_total_attempts": 2,
            "prior_request_invariants_hash": content_hash(
                prior_request_invariants
            ),
            "request_invariants_hash": content_hash(
                continuation_request_invariants
            ),
            "package_semantic_intent": base_request["semantic_intent"],
            "approved_stock_search_intent": (
                command.approved_stock_search_intent
            ),
            "approved_query_authority": deepcopy(approved_query_authority),
            "base_query_evidence": deepcopy(base_query_evidence),
            "query_material_diff": deepcopy(query_material_diff),
            "stock_search_intent_derivation": deepcopy(
                stock_search_intent_derivation
            ),
            "query_intent_coverage_evidence": deepcopy(
                approved_query_intent_coverage
            ),
            "approved_pending_scene_stock_search_intents": deepcopy(
                command.approved_pending_scene_stock_search_intents
            ),
            "pending_query_amendments": deepcopy(
                pending_query_amendments
            ),
            "semantic_fit_threshold": base_request["semantic_fit_threshold"],
            "canonical_timeline_hash": base_request["canonical_timeline_hash"],
            "automatic_retry_allowed": False,
            "provider_substitution_allowed": False,
            "automatic_pexels_to_ai_fallback": False,
            "incremental_cost_cap_usd": 0.0,
            "youtube_upload_authorized": False,
            "publish_execution_authorized": False,
        }
        authorization_hash = content_hash(authorization_scope)
        candidates = [
            item
            for item in self.session.scalars(
                select(ApprovalDecision).where(
                    ApprovalDecision.target_artifact_version_id
                    == review_manifest_version.id
                )
            ).all()
            if (item.metadata_ or {}).get("approval_scope")
            == PROVIDER_ATTEMPT_CONTINUATION_SCOPE
            and (item.metadata_ or {}).get("authorization_content_hash")
            == authorization_hash
        ]
        if len(candidates) > 1:
            raise ValidationFailureError("MR1_PROVIDER_CONTINUATION_APPROVAL_DUPLICATE")
        continuation_approval = candidates[0] if candidates else None
        if continuation_approval is not None and not (
            self._provider_continuation_decision_exact(
                decision=continuation_approval,
                state=state,
                authorization_scope=authorization_scope,
                authorization_hash=authorization_hash,
                review_manifest_version=review_manifest_version,
                expected_operator_id=command.decided_by_user_id,
                expected_review_task_id=review_task.id,
            )
        ):
            raise ValidationFailureError(
                "MR1_PROVIDER_CONTINUATION_APPROVAL_INVALID"
            )
        if continuation_approval is None:
            continuation_approval = ApprovalService(
                self.session
            ).create_approval_decision(
                data=ApprovalDecisionCreate(
                    target_type="artifact_version",
                    target_id=review_manifest_version.id,
                    target_artifact_version_id=review_manifest_version.id,
                    decision="approved",
                    decided_by_user_id=command.decided_by_user_id,
                    rationale=(
                        "Operator-approved continuation for exactly one additional "
                        f"Pexels {scene_id} search attempt on the existing MR1 run."
                    ),
                    metadata={
                        "approval_ref": (
                            PROVIDER_CONTINUATION_APPROVAL_REF_PREFIX
                            + f"{state['run_id']}/{scene_id.lower()}/attempt-2/"
                            + review_manifest_version.content_hash
                        ),
                        "approval_scope": (PROVIDER_ATTEMPT_CONTINUATION_SCOPE),
                        "authorization_content_hash": authorization_hash,
                        "operator_review_manifest_artifact_version_id": (
                            str(review_manifest_version.id)
                        ),
                        "operator_review_manifest_content_hash": (
                            review_manifest_version.content_hash
                        ),
                        "operator_review_task_id": str(review_task.id),
                        "run_id": state["run_id"],
                        "operation_key": operation_key,
                        "additional_attempts": 1,
                        "maximum_total_attempts": 2,
                        "operator_decision_text": (command.operator_decision_text),
                        "approved_stock_search_intent": (
                            command.approved_stock_search_intent
                        ),
                        "approved_query_authority_hash": content_hash(
                            approved_query_authority
                        ),
                        "pending_query_amendments_hash": content_hash(
                            pending_query_amendments
                        ),
                        "automatic_retry_allowed": False,
                        "provider_substitution_allowed": False,
                        "publish_execution_authorized": False,
                    },
                    decision_basis=deepcopy(authorization_scope),
                    evidence_basis={
                        "operator_review_manifest_artifact_version_id": (
                            str(review_manifest_version.id)
                        ),
                        "operator_review_manifest_content_hash": (
                            review_manifest_version.content_hash
                        ),
                        "operator_review_task_id": str(review_task.id),
                        "prior_attempt_artifact_version_id": str(
                            prior_attempt_version.id
                        ),
                        "prior_attempt_content_hash": (
                            prior_attempt_version.content_hash
                        ),
                        "prior_failure": ledger["failure"],
                    },
                    policy_basis={
                        "prior_request_invariants": prior_request_invariants,
                        "exact_request_invariants": (
                            continuation_request_invariants
                        ),
                        "approved_query_authority": deepcopy(
                            approved_query_authority
                        ),
                        "pending_query_amendments": deepcopy(
                            pending_query_amendments
                        ),
                        "no_fallback": True,
                        "no_provider_substitution": True,
                        "no_youtube_upload": True,
                    },
                    context_pack_ref=(f"artifact-version://{prior_attempt_version.id}"),
                    human_decision_note=(
                        "The operator explicitly supplied the continuation "
                        "approval text; Codex persisted but did not originate it."
                    ),
                ),
                assigned_final_review_task_id=review_task.id,
                correlation_id=(
                    f"mr1-provider-continuation-{state['run_id']}-{scene_id}"
                ),
            )
            if not self._provider_continuation_decision_exact(
                decision=continuation_approval,
                state=state,
                authorization_scope=authorization_scope,
                authorization_hash=authorization_hash,
                review_manifest_version=review_manifest_version,
                expected_operator_id=command.decided_by_user_id,
                expected_review_task_id=review_task.id,
            ):
                raise ValidationFailureError(
                    "MR1_PROVIDER_CONTINUATION_APPROVAL_INVALID"
                )

        review_task = ReviewService(self.session).complete_review_task(
            review_task_id=review_task.id,
            actor_user_id=command.decided_by_user_id,
            resolution_ref=self._provider_continuation_review_resolution_ref(
                review_manifest_version
            ),
            approval_decision_ids=[continuation_approval.id],
            correlation_id=(
                f"mr1-provider-continuation-review-complete-"
                f"{state['run_id']}-{scene_id}"
            ),
        )
        if not self._provider_continuation_review_task_exact(
            task=review_task,
            state=state,
            review_manifest_version=review_manifest_version,
            prior_consumed_snapshot=prior_consumed_snapshot,
            expected_operator_id=command.decided_by_user_id,
            expected_approval_decision_id=continuation_approval.id,
            require_completed=True,
        ):
            raise ValidationFailureError(
                "MR1_PROVIDER_CONTINUATION_REVIEW_TASK_INVALID"
            )

        receipt = {
            **authorization_scope,
            "approval_decision_id": str(continuation_approval.id),
            "authorization_content_hash": authorization_hash,
            "decided_by_user_id": str(continuation_approval.decided_by_user_id),
            "decided_at": continuation_approval.decided_at.isoformat(),
        }
        receipt["receipt_content_hash"] = content_hash(receipt)
        supplemental_key = f"pexels:{scene_id}:supplement:02"
        budget_evidence = deepcopy(ledger["monthly_budget_evidence"])
        budget_evidence["operation_key"] = supplemental_key
        budget_evidence["content_hash"] = content_hash(
            {
                key: value
                for key, value in budget_evidence.items()
                if key != "content_hash"
            }
        )
        supplemental_ledger = {
            "schema_version": "mr1.provider-attempt-ledger.v1",
            "run_id": state["run_id"],
            "operation_key": supplemental_key,
            "provider": "pexels_api",
            "operation": "supporting_asset_acquisition",
            "scene_id": scene_id,
            "provider_attempt_ordinal": 2,
            "attempt_cap": 1,
            "attempt_count": 0,
            "network_submit_started": False,
            "search_submit_count": 0,
            "download_submit_count": 0,
            "pre_submit_failures": 0,
            "state": "PLANNED",
            "submit_state": "NOT_SUBMITTED",
            "idempotency_key": (
                f"mr1:{state['run_id']}:pexels:{scene_id}:attempt-2"
            ),
            "idempotency_fingerprint": _idempotency_fingerprint(
                approval_content_hash=authorization_hash,
                run_id=state["run_id"],
                provider="pexels_api",
                operation="supporting_asset_acquisition",
                scene_id=scene_id,
            ),
            "idempotency_authority_content_hash": authorization_hash,
            "idempotency_fingerprint_contract": (IDEMPOTENCY_FINGERPRINT_CONTRACT),
            "idempotency_fingerprint_serialization": (
                IDEMPOTENCY_FINGERPRINT_SERIALIZATION
            ),
            "approval_id": state["approval_id"],
            "approval_content_hash": state["approval_content_hash"],
            "provider_attempt_continuation": deepcopy(receipt),
            "supersedes_no_attempt": True,
            "prior_consumed_attempt": deepcopy(prior_consumed_snapshot),
            "hard_cap_usd": ledger["hard_cap_usd"],
            "monthly_budget_state": "PASS",
            "monthly_budget_evidence": budget_evidence,
            "monthly_budget_latest_evidence": deepcopy(
                ledger.get("monthly_budget_latest_evidence")
            ),
            "provider_substitution_allowed": False,
            "automatic_retry_allowed": False,
        }
        supplemental_artifact, supplemental_version = self._create_artifact(
            project_id=uuid.UUID(state["project_id"]),
            artifact_type=ATTEMPT_ARTIFACT_TYPE,
            actor_id=base_approval.decided_by_user_id,
            content=supplemental_ledger,
            correlation_id=(
                f"mr1-attempt-{state['run_id']}-pexels-{scene_id}-supplement-02"
            ),
        )
        supplemental_ledger["artifact_version_id"] = str(supplemental_version.id)
        state["attempts"][supplemental_key] = supplemental_ledger
        state["attempt_artifact_ids"][supplemental_key] = str(supplemental_artifact.id)
        state.setdefault("active_provider_attempt_keys", {})[operation_key] = (
            supplemental_key
        )
        for pending_scene_id in sorted(pending_query_amendments):
            pending_operation_key = f"pexels:{pending_scene_id}"
            pending_ledger = state["attempts"][pending_operation_key]
            pending_ledger["provider_query_amendment"] = deepcopy(receipt)
            pending_ledger["idempotency_authority_content_hash"] = (
                authorization_hash
            )
            pending_ledger["idempotency_fingerprint"] = (
                _idempotency_fingerprint(
                    approval_content_hash=authorization_hash,
                    run_id=state["run_id"],
                    provider="pexels_api",
                    operation="supporting_asset_acquisition",
                    scene_id=pending_scene_id,
                )
            )
            self._save_attempt(
                state,
                pending_operation_key,
                base_approval.decided_by_user_id,
            )
        state["provider_attempt_continuation_approvals"] = [deepcopy(receipt)]
        if state.get("blocker"):
            state.setdefault("blocker_history", []).append(
                {
                    "blocker": state["blocker"],
                    "resolved_by_continuation_approval_id": str(
                        continuation_approval.id
                    ),
                }
            )
        state.pop("blocker", None)
        state["current_state"] = "PROVIDER_ATTEMPT_CONTINUATION_APPROVED"
        state.setdefault("event_order", []).extend(
            f"PEXELS_{pending_scene_id}_QUERY_AMENDMENT_APPROVED"
            for pending_scene_id in sorted(pending_query_amendments)
        )
        state.setdefault("event_order", []).append(
            f"PEXELS_{scene_id}_CONTINUATION_APPROVED"
        )
        _write_json_atomic(
            Path(state["workspace"]) / "provider_attempt_continuation_approval.json",
            receipt,
        )
        self._save_run(
            run_artifact,
            state,
            actor_id=base_approval.decided_by_user_id,
        )
        self._durable_boundary()
        return deepcopy(receipt)

    def closeout(
        self,
        command: MR1FinalMediaCloseoutCommand,
        *,
        drive_gateway: Any | None = None,
    ) -> dict[str, Any]:
        run_artifact, run_version = self._require_run(command.run_id, lock=True)
        state = deepcopy(run_version.content or {})
        if state.get("project_id") != str(command.project_id):
            raise ValidationFailureError("MR1_CLOSEOUT_PROJECT_MISMATCH")
        current_candidate = state.get("review_media_candidate") or {}
        current_drive = state.get("drive_archive") or {}
        if current_candidate.get("artifact_version_id") != str(
            command.review_media_candidate_artifact_version_id
        ):
            raise ValidationFailureError(
                "MR1_CLOSEOUT_CURRENT_REVIEW_ROUND_CANDIDATE_REQUIRED"
            )
        if (
            current_candidate.get("content_hash")
            != command.review_media_candidate_content_hash
        ):
            raise ValidationFailureError(
                "MR1_CLOSEOUT_REVIEW_MEDIA_CANDIDATE_HASH_MISMATCH"
            )
        if current_drive.get("artifact_version_id") != str(
            command.drive_archive_receipt_artifact_version_id
        ):
            raise ValidationFailureError(
                "MR1_CLOSEOUT_CURRENT_REVIEW_ROUND_DRIVE_REQUIRED"
            )
        if current_drive.get("content_hash") != (
            command.drive_archive_receipt_content_hash
        ):
            raise ValidationFailureError(
                "MR1_CLOSEOUT_DRIVE_RECEIPT_HASH_MISMATCH"
            )
        if state.get("archive_identity") != command.archive_identity:
            raise ValidationFailureError(
                "MR1_CLOSEOUT_ARCHIVE_IDENTITY_MISMATCH"
            )
        if state.get("current_state") == "FINAL_MEDIA_REGISTERED":
            return self._read_existing_closeout(state, command)
        existing_receipt = self._find_human_receipt(command.run_id)
        if state.get("current_state") in {
            "REPAIR_REQUIRED_AFTER_HUMAN_REJECTION",
            "BLOCKED_REQUIRES_PACKAGE_REVISION_AND_NEW_APPROVAL",
        } and self._human_receipt_matches_command(existing_receipt, command):
            return self._read_existing_closeout(state, command)
        closeout_boundary = state.get("current_state") or state.get("state")
        if closeout_boundary not in {
            "AWAITING_HUMAN_FULL_WATCH",
            "WAITING_HUMAN_REVIEW",
            "FINALIZING_ARCHIVE_SUPPLEMENT",
        }:
            raise ValidationFailureError("MR1_CLOSEOUT_NOT_WAITING_HUMAN_REVIEW")
        if command.decision == "PASS" and not callable(
            getattr(
                drive_gateway,
                "upload_finalization_supplement_and_verify",
                None,
            )
        ):
            raise ValidationFailureError("MR1_FINALIZATION_DRIVE_GATEWAY_REQUIRED")

        candidate = self._exact_version(
            command.review_media_candidate_artifact_version_id,
            command.review_media_candidate_content_hash,
            CANDIDATE_ARTIFACT_TYPE,
            command.project_id,
        )
        drive = self._exact_version(
            command.drive_archive_receipt_artifact_version_id,
            command.drive_archive_receipt_content_hash,
            DRIVE_RECEIPT_ARTIFACT_TYPE,
            command.project_id,
        )
        candidate_content = deepcopy(candidate.content or {})
        drive_content = deepcopy(drive.content or {})
        # The command bindings alone are not candidate authority. Reopen the
        # complete candidate first; its validator preserves a dedicated
        # technical-QC error for source-evidence failures.
        self._validate_candidate_payload(state, candidate_content)
        technical_qc = self._require_technical_qc_authority(
            state=state,
            candidate=candidate,
            candidate_content=candidate_content,
        )
        self._validate_closeout_bindings(
            command=command,
            state=state,
            candidate=candidate,
            candidate_content=candidate_content,
            drive=drive,
            drive_content=drive_content,
        )
        resume_pass_closeout = bool(
            command.decision == "PASS"
            and closeout_boundary == "FINALIZING_ARCHIVE_SUPPLEMENT"
            and self._human_receipt_matches_command(existing_receipt, command)
        )
        if (
            self._human_receipt_matches_candidate(existing_receipt, candidate)
            and not resume_pass_closeout
        ):
            return self._read_existing_closeout(state, command)

        final_review_task = self._require_final_human_review_task(
            state=state,
            candidate=candidate,
            drive=drive,
            decided_by_user_id=command.decided_by_user_id,
            allow_completed=resume_pass_closeout,
        )

        review_round = int(state.get("review_round") or 1)
        rejection_classification = (
            self._classify_human_rejection(command.operator_decision_text)
            if command.decision == "REJECT"
            else None
        )
        receipt_content = {
            "schema_version": "mr1.human-full-watch-receipt.v1",
            "run_id": str(command.run_id),
            "project_id": str(command.project_id),
            "decision": command.decision,
            "decision_source": command.decision_source,
            "review_authority": command.review_authority,
            "operator_decision_text": command.operator_decision_text,
            "reviewed_output_sha256": command.reviewed_output_sha256,
            "review_media_candidate": {
                "artifact_version_id": str(candidate.id),
                "content_hash": candidate.content_hash,
            },
            "drive_archive_receipt": {
                "artifact_version_id": str(drive.id),
                "content_hash": drive.content_hash,
                "archive_identity": command.archive_identity,
            },
            "technical_media_qc": {
                "artifact_version_id": str(technical_qc.id),
                "content_hash": technical_qc.content_hash,
                "result": "PASS",
            },
            "archive_identity": command.archive_identity,
            "review_round": review_round,
            "final_human_review_task": {
                "review_task_id": str(final_review_task.id),
                "assigned_to_user_id": str(final_review_task.assigned_to_user_id),
                "target_artifact_version_id": str(
                    final_review_task.target_artifact_version_id
                ),
                "review_round": review_round,
                "status_before_decision": final_review_task.status,
            },
            "rejection_classification": rejection_classification,
            "technical_qc_result": "PASS",
            "creative_review_result": (
                "ACCEPTED" if command.decision == "PASS" else "REJECTED"
            ),
            "archive_verification_result": "PASS",
            "decided_by_user_id": str(command.decided_by_user_id),
            "decided_at": datetime.now(UTC).isoformat(),
            "youtube_upload_authorized": False,
            "publish_execution_authorized": False,
            "youtube_calls": 0,
        }
        if resume_pass_closeout:
            if existing_receipt is None:
                raise ValidationFailureError("MR1_FINALIZATION_HUMAN_RECEIPT_MISSING")
            human_receipt = self._exact_version(
                existing_receipt.id,
                existing_receipt.content_hash,
                HUMAN_RECEIPT_ARTIFACT_TYPE,
                command.project_id,
            )
            resolution_ref = self._human_review_resolution_ref(human_receipt)
            if final_review_task.status != "completed" or not any(
                item.get("type") == "mr1_human_full_watch_receipt"
                and item.get("artifact_version_id") == str(human_receipt.id)
                and item.get("content_hash") == human_receipt.content_hash
                and item.get("resolution_ref") == resolution_ref
                for item in final_review_task.evidence_refs or []
            ):
                raise ValidationFailureError(
                    "MR1_FINALIZATION_HUMAN_RECEIPT_AUTHORITY_INVALID"
                )
        else:
            human_receipt = self._persist_human_receipt(
                state=state,
                content=receipt_content,
                actor_id=command.decided_by_user_id,
            )
            resolution_ref = self._human_review_resolution_ref(human_receipt)
            final_review_task = ReviewService(self.session).complete_review_task(
                review_task_id=final_review_task.id,
                actor_user_id=command.decided_by_user_id,
                resolution_ref=resolution_ref,
                approval_decision_ids=[uuid.UUID(str(state["approval_id"]))],
                correlation_id=(
                    f"mr1-final-human-complete-{state['run_id']}-round-{review_round}"
                ),
            )
            human_evidence = {
                "type": "mr1_human_full_watch_receipt",
                "artifact_version_id": str(human_receipt.id),
                "content_hash": human_receipt.content_hash,
                "decision": command.decision,
                "decided_by_user_id": str(command.decided_by_user_id),
                "review_round": review_round,
                "resolution_ref": resolution_ref,
            }
            existing_task_evidence = list(final_review_task.evidence_refs or [])
            if human_evidence not in existing_task_evidence:
                final_review_task.evidence_refs = [
                    *existing_task_evidence,
                    human_evidence,
                ]
            self.session.flush()

        if command.decision == "REJECT":
            state["human_review"] = "REJECT"
            state["human_review_receipt_artifact_version_id"] = str(human_receipt.id)
            state["human_review_receipt_content_hash"] = human_receipt.content_hash
            prior_round = {
                "review_round": review_round,
                "decision": "REJECT",
                "classification": rejection_classification,
                "operator_decision_text": command.operator_decision_text,
                "review_media_candidate": deepcopy(state.get("review_media_candidate")),
                "drive_archive": deepcopy(state.get("drive_archive")),
                "local_result": deepcopy(state.get("local_result")),
                "human_review_receipt_artifact_version_id": str(human_receipt.id),
                "human_review_receipt_content_hash": human_receipt.content_hash,
            }
            state.setdefault("review_round_history", []).append(prior_round)
            state.setdefault("event_order", []).append("HUMAN_FULL_WATCH_REJECT")
            if rejection_classification != "DETERMINISTIC_LOCAL_REPAIR":
                state["current_state"] = (
                    "BLOCKED_REQUIRES_PACKAGE_REVISION_AND_NEW_APPROVAL"
                )
                state["state"] = state["current_state"]
                state["blocker"] = (
                    "HUMAN_REJECT_REQUIRES_APPROVED_SOURCE_OR_PACKAGE_CHANGE"
                )
                state["event_order"].append("HUMAN_REJECT_PACKAGE_REVISION_REQUIRED")
                saved = self._save_run(
                    run_artifact, state, actor_id=command.decided_by_user_id
                )
                return self._public_result(saved, state)

            next_round = review_round + 1
            normalized_reason = command.operator_decision_text.casefold()
            repair_class_terms = (
                ("caption", "caption"),
                ("overlay", "overlay"),
                ("crop", "crop"),
                ("motion", "motion"),
                ("transition", "transition"),
                ("render parameter", "render_parameters"),
                ("readability", "readability"),
                ("archive package", "archive_package"),
            )
            repair_classes = [
                repair_class
                for term, repair_class in repair_class_terms
                if term in normalized_reason
            ]
            directive = {
                "schema_version": "mr1.human-repair-directive.v1",
                "run_id": str(command.run_id),
                "decision": "REJECT",
                "review_round": next_round,
                "rejected_output_sha256": command.reviewed_output_sha256,
                "repair_classes": repair_classes,
                "operator_reason": command.operator_decision_text,
            }
            directive["content_hash"] = content_hash(directive)
            directive_path = Path(state["workspace"]) / "human_repair_directive.json"
            _write_json_atomic(directive_path, directive)
            state["review_round"] = next_round
            state["render_identity"] = (
                f"mr1-render://small-team-ai/{state['run_id']}/v{next_round}"
            )
            state["current_state"] = "REPAIR_REQUIRED_AFTER_HUMAN_REJECTION"
            state["state"] = state["current_state"]
            state["human_repair_directive"] = {
                **directive,
                "path": str(directive_path.resolve(strict=True)),
            }
            state["local_result"] = {
                "state": "REPAIR_REQUIRED_AFTER_HUMAN_REJECTION",
                "resume_from": "REPAIR_REQUIRED_AFTER_HUMAN_REJECTION",
                "human_repair_directive": deepcopy(state["human_repair_directive"]),
                "provider_outputs_durable": True,
            }
            state["review_media_candidate"] = None
            state["drive_archive"] = None
            # Runs created by this state machine always carry an immutable Drive
            # attempt ledger.  Older/synthetic closeout-boundary records may not;
            # do not fabricate or reopen a ledger for those records merely to
            # persist the human rejection receipt.  When the ledger is present,
            # keep the existing resumable reconciliation semantics and fail
            # closed if its durable attempt artifact is incomplete.
            if "attempts" in state:
                attempts = state["attempts"]
                if not isinstance(attempts, dict) or not isinstance(
                    attempts.get("google_drive:archive"), dict
                ):
                    raise ValidationFailureError("MR1_DRIVE_ATTEMPT_LEDGER_MISSING")
                if "google_drive:archive" not in (
                    state.get("attempt_artifact_ids") or {}
                ):
                    raise ValidationFailureError("MR1_DRIVE_ATTEMPT_ARTIFACT_MISSING")
                drive_ledger = attempts["google_drive:archive"]
                drive_ledger["state"] = "RESUMABLE_FAILURE"
                drive_ledger["submit_state"] = "RESUMABLE_FAILURE"
                drive_ledger["resume_reason"] = "HUMAN_REJECT_LOCAL_ARCHIVE_RECONCILE"
                drive_ledger["prior_verified_output"] = deepcopy(
                    drive_ledger.get("output")
                )
                drive_ledger.pop("output", None)
                self._save_attempt(
                    state,
                    "google_drive:archive",
                    command.decided_by_user_id,
                )
                finalization_ledger = attempts.get(MR1_DRIVE_FINALIZATION_OPERATION_KEY)
                if finalization_ledger is not None:
                    if (
                        not isinstance(finalization_ledger, dict)
                        or finalization_ledger.get("state") != "WAITING_HUMAN_PASS"
                        or finalization_ledger.get("submit_state") != "NOT_SUBMITTED"
                        or finalization_ledger.get("attempt_count") != 0
                        or finalization_ledger.get("network_submit_started")
                        is not False
                        or finalization_ledger.get("review_round") != review_round
                        or MR1_DRIVE_FINALIZATION_OPERATION_KEY
                        not in (state.get("attempt_artifact_ids") or {})
                    ):
                        raise ValidationFailureError(
                            "MR1_FINALIZATION_ATTEMPT_ROUND_REBIND_INVALID"
                        )
                    finalization_ledger["review_round"] = next_round
                    finalization_ledger["idempotency_key"] = (
                        mr1_drive_finalization_idempotency_key(
                            run_id=state["run_id"],
                            review_round=next_round,
                        )
                    )
                    self._save_attempt(
                        state,
                        MR1_DRIVE_FINALIZATION_OPERATION_KEY,
                        command.decided_by_user_id,
                    )
            state.setdefault("repair_cycles", []).append(
                {
                    "stage": "HUMAN_FULL_WATCH",
                    "classification": "DETERMINISTIC_LOCAL",
                    "reason": command.operator_decision_text,
                    "provider_calls_repeated": False,
                    "review_round": review_round,
                    "recorded_at": datetime.now(UTC).isoformat(),
                }
            )
            state["event_order"].append("HUMAN_REJECT_LOCAL_REPAIR_REQUIRED")
            saved = self._save_run(
                run_artifact, state, actor_id=command.decided_by_user_id
            )
            return self._public_result(saved, state)

        output_path = Path(candidate_content["output_file_ref"])
        project = self.session.get(VideoProject, command.project_id)
        if project is None:
            raise ValidationFailureError("MR1_FINAL_MEDIA_PROJECT_MISSING")
        frozen_lineage = self._revalidate_final_media_lineage_authority(state)
        final_drive_proof = self._exact_drive_final_media_proof(
            candidate_content=candidate_content,
            drive_content=drive_content,
        )
        if resume_pass_closeout:
            cloud_ref = self._reopen_final_cloud_media_ref_for_resume(
                state=state,
                project=project,
                candidate=candidate,
                drive=drive,
                technical_qc=technical_qc,
                human_receipt=human_receipt,
                frozen_lineage=frozen_lineage,
                final_drive_proof=final_drive_proof,
            )
        else:
            cloud_ref = self._create_final_cloud_media_ref(
                state=state,
                project=project,
                candidate=candidate,
                drive=drive,
                technical_qc=technical_qc,
                human_receipt=human_receipt,
                frozen_lineage=frozen_lineage,
                final_drive_proof=final_drive_proof,
            )
        final_lineage = self._persist_final_media_lineage(
            state=state,
            candidate=candidate,
            candidate_content=candidate_content,
            drive=drive,
            technical_qc=technical_qc,
            human_receipt=human_receipt,
            cloud_ref=cloud_ref,
            frozen_lineage=frozen_lineage,
            final_drive_proof=final_drive_proof,
            actor_id=command.decided_by_user_id,
        )
        state["human_review"] = "PASS"
        state["human_review_receipt_artifact_version_id"] = str(human_receipt.id)
        state["human_review_receipt_content_hash"] = human_receipt.content_hash
        state["final_cloud_media_ref_id"] = str(cloud_ref.id)
        state["final_media_lineage"] = {
            "artifact_version_id": str(final_lineage.id),
            "content_hash": final_lineage.content_hash,
        }
        state["current_state"] = "FINALIZING_ARCHIVE_SUPPLEMENT"
        state["state"] = "FINALIZING_ARCHIVE_SUPPLEMENT"
        final_archive_supplement = self._finalize_archive_supplement(
            run_artifact=run_artifact,
            state=state,
            candidate=candidate,
            drive=drive,
            human_receipt=human_receipt,
            final_lineage=final_lineage,
            drive_gateway=drive_gateway,
            actor_id=command.decided_by_user_id,
        )
        state["final_archive_supplement"] = {
            "artifact_version_id": str(final_archive_supplement.id),
            "content_hash": final_archive_supplement.content_hash,
        }
        final_ref = FinalMediaRefService(self.session).create(
            data=FinalMediaRefCreate(
                company_id=project.company_id,
                channel_workspace_id=project.channel_workspace_id,
                video_project_id=command.project_id,
                media_type="LONG_FORM_FINAL",
                file_ref=str(output_path),
                duration_seconds=(
                    Decimal(str(candidate_content["duration_seconds"]))
                    if candidate_content.get("duration_seconds") is not None
                    else None
                ),
                aspect_ratio="16:9",
                resolution="1920x1080",
                provider_key="mr1-native-ffmpeg-renderer",
                provider_type="LOCAL_RENDERER_CAPABILITY",
                checksum_sha256=command.reviewed_output_sha256,
                media_qc_report_id=None,
                cloud_media_ref_id=cloud_ref.id,
                lineage_artifact_version_id=final_lineage.id,
            )
        )
        state["current_state"] = "FINAL_MEDIA_REGISTERED"
        state["state"] = "FINAL_MEDIA_REGISTERED"
        state["final_media_ref_id"] = str(final_ref.id)
        state.setdefault("event_order", []).extend(
            [
                "HUMAN_FULL_WATCH_PASS",
                "FINAL_CLOUD_MEDIA_REF_VERIFIED",
                "FINAL_MEDIA_LINEAGE_RECEIPT_CREATED",
                "FINAL_ARCHIVE_SUPPLEMENT_VERIFIED",
                "FINAL_MEDIA_REF_CREATED",
            ]
        )
        state["proceed_to_destination_closeout"] = True
        state["proceed_to_pub1"] = False
        saved = self._save_run(run_artifact, state, actor_id=command.decided_by_user_id)
        self._durable_boundary()
        return self._public_result(saved, state)

    @staticmethod
    def _classify_human_rejection(operator_text: str) -> str:
        normalized = " ".join(operator_text.casefold().replace("_", " ").split())
        package_terms = {
            "script",
            "narration",
            "new voice",
            "provider generation",
            "new provider",
            "visual route",
            "metadata",
            "thumbnail",
        }
        deterministic_terms = {
            "caption",
            "overlay",
            "crop",
            "motion",
            "transition",
            "render parameter",
            "readability",
            "archive package",
        }
        if any(term in normalized for term in package_terms):
            return "PACKAGE_REVISION_AND_NEW_APPROVAL_REQUIRED"
        if any(term in normalized for term in deterministic_terms):
            return "DETERMINISTIC_LOCAL_REPAIR"
        return "PACKAGE_REVISION_AND_NEW_APPROVAL_REQUIRED"

    def _execute(
        self,
        *,
        run_artifact: Artifact,
        run_version: ArtifactVersion,
        state: dict[str, Any],
        authority: dict[str, Any],
        gateways: MR1ProviderGateways,
        actor_id: uuid.UUID,
    ) -> dict[str, Any]:
        workspace = Path(state["workspace"])
        visual_routes = self._visual_route_authority(authority)

        if "narration" not in state["provider_outputs"]:
            request = self._narration_request(state, authority, workspace)
            ok, output, run_version = self._execute_one_shot(
                run_artifact=run_artifact,
                state=state,
                run_version=run_version,
                actor_id=actor_id,
                operation_key="elevenlabs:narration",
                event="ELEVENLABS_NARRATION",
                counter="elevenlabs",
                request=request,
                invoke=lambda before_submit: gateways.narration.execute_once(
                    request=deepcopy(request),
                    destination=workspace / "narration" / "narration.mp3",
                    before_submit=before_submit,
                ),
            )
            if not ok:
                return self._public_result(run_version, state)
            state["provider_outputs"]["narration"] = output
            runtime_gate = self._narration_runtime_gate(authority, output)
            state["narration_runtime_gate"] = runtime_gate
            if runtime_gate["result"] != "PASS":
                self._settle_budget_consumed_failure(state)
                state["current_state"] = "BLOCKED_RUNTIME_OUTSIDE_APPROVED_HARD_LIMITS"
                state["blocker"] = "NARRATION_DURATION_OUTSIDE_APPROVED_6_TO_12_MINUTES"
                state["event_order"].append("NARRATION_RUNTIME_HARD_GATE_BLOCK")
                run_version = self._save_run(run_artifact, state, actor_id=actor_id)
                self._durable_boundary()
                return self._public_result(run_version, state)
            state["current_state"] = "NARRATION_READY"
            state["event_order"].append("NARRATION_RUNTIME_HARD_GATE_PASS")
            run_version = self._save_run(run_artifact, state, actor_id=actor_id)

        if "alignment" not in state["provider_outputs"]:
            request = self._alignment_request(state, authority, workspace)
            ok, output, run_version = self._execute_one_shot(
                run_artifact=run_artifact,
                state=state,
                run_version=run_version,
                actor_id=actor_id,
                operation_key="elevenlabs:forced_alignment",
                event="ELEVENLABS_FORCED_ALIGNMENT",
                counter="forced_alignment",
                request=request,
                invoke=lambda before_submit: gateways.alignment.execute_once(
                    request=deepcopy(request),
                    audio_path=Path(
                        state["provider_outputs"]["narration"].get("audio_path")
                        or state["provider_outputs"]["narration"].get("output_path")
                        or state["provider_outputs"]["narration"].get("audio_asset_ref")
                    ),
                    before_submit=before_submit,
                ),
            )
            if not ok:
                return self._public_result(run_version, state)
            state["provider_outputs"]["alignment"] = output
            if (
                state["provider_outputs"]["narration"].get(
                    "offline_recovery_from_consumed_attempt"
                )
                is True
            ):
                self._bind_recovered_timing_seed_from_forced_alignment(
                    state=state,
                    alignment_output=output,
                )
            state["current_state"] = "ALIGNMENT_READY"
            run_version = self._save_run(run_artifact, state, actor_id=actor_id)

        if state.get("temporal_authority") is None:
            prepare_temporal = getattr(
                self.local_continuation,
                "prepare_temporal_authority_once",
                None,
            )
            if not callable(prepare_temporal):
                state["current_state"] = "REPAIRABLE_LOCAL_FAILURE"
                state["local_result"] = {
                    "state": "REPAIRABLE_LOCAL_FAILURE",
                    "failed_stage": "CANONICAL_MEDIA_TIMELINE",
                    "resume_from": "ALIGNMENT_READY",
                    "reason": "MR1_TEMPORAL_PREPASS_REQUIRED_BEFORE_VISUAL_PROVIDER",
                }
                run_version = self._save_run(run_artifact, state, actor_id=actor_id)
                return self._public_result(run_version, state)
            try:
                temporal = _jsonable(
                    prepare_temporal(
                        run_id=uuid.UUID(state["run_id"]),
                        workspace=workspace,
                        authority=deepcopy(authority),
                        provider_outputs=deepcopy(state["provider_outputs"]),
                    )
                )
                self._validate_temporal_prepass(state, temporal)
            except Exception as exc:
                state["current_state"] = "REPAIRABLE_LOCAL_FAILURE"
                state["local_result"] = {
                    "state": "REPAIRABLE_LOCAL_FAILURE",
                    "failed_stage": "CANONICAL_MEDIA_TIMELINE",
                    "resume_from": "ALIGNMENT_READY",
                    "reason": f"{type(exc).__name__}:{exc}",
                }
                state["repair_cycles"].append(
                    {
                        "stage": "CANONICAL_MEDIA_TIMELINE",
                        "classification": "DETERMINISTIC_LOCAL",
                        "reason": f"{type(exc).__name__}:{exc}",
                        "provider_calls_repeated": False,
                        "recorded_at": datetime.now(UTC).isoformat(),
                    }
                )
                run_version = self._save_run(run_artifact, state, actor_id=actor_id)
                return self._public_result(run_version, state)
            state["temporal_authority"] = temporal
            state["current_state"] = "CANONICAL_TIMELINE_READY"
            state["event_order"].append("CANONICAL_TIMELINE_PASS")
            run_version = self._save_run(run_artifact, state, actor_id=actor_id)
            self._durable_boundary()

        for scene_id in visual_routes.pexels_scenes:
            output_key = f"pexels:{scene_id}"
            if output_key in state["provider_outputs"]:
                continue
            operation_key = (state.get("active_provider_attempt_keys") or {}).get(
                output_key
            ) or output_key
            request = self._pexels_request(
                state,
                authority,
                scene_id,
                workspace,
                operation_key=operation_key,
            )
            ok, output, run_version = self._execute_pexels_once(
                run_artifact=run_artifact,
                state=state,
                run_version=run_version,
                actor_id=actor_id,
                scene_id=scene_id,
                operation_key=operation_key,
                request=request,
                invoke=lambda before_search, before_download, scene_id=scene_id, request=request: (
                    gateways.pexels.acquire_scene_once(
                        request=deepcopy(request),
                        destination=workspace / "source_assets" / f"{scene_id}.mp4",
                        before_search_submit=before_search,
                        before_download_submit=before_download,
                    )
                ),
            )
            if not ok:
                return self._public_result(run_version, state)
            state["provider_outputs"][output_key] = output
            state["scene_executions"][scene_id] = {
                "scene_id": scene_id,
                "route": "PEXELS_VIDEO",
                "provider": "pexels_api",
                "status": "PASS",
                "fallback_used": False,
            }
            run_version = self._save_run(run_artifact, state, actor_id=actor_id)

        for scene_id in visual_routes.native_scenes:
            state["scene_executions"].setdefault(
                scene_id,
                {
                    "scene_id": scene_id,
                    "route": visual_routes.routes[scene_id],
                    "provider": "native",
                    "status": "PLANNED_LOCAL",
                    "fallback_used": False,
                },
            )
        state["current_state"] = "PROVIDER_EXECUTION_COMPLETE"
        self._settle_budget_actual_if_known(state)
        run_version = self._save_run(run_artifact, state, actor_id=actor_id)
        execution_evidence = workspace / "execution_evidence"
        _write_json_atomic(
            execution_evidence / "monthly_budget_reservation.json",
            deepcopy(state["monthly_budget_reservation"]),
        )
        attempt_evidence_path = execution_evidence / "provider_attempt_ledgers.json"
        provider_output_path = execution_evidence / "provider_output_manifest.json"
        drive_ledger_state = (
            (state.get("attempts") or {}).get("google_drive:archive", {}).get("state")
        )
        frozen_drive_resume = bool(
            drive_ledger_state in {"MUTATING_RESUMABLE_ARCHIVE", "RESUMABLE_FAILURE"}
            and (state.get("attempts") or {})
            .get("google_drive:archive", {})
            .get("resume_reason")
            != "HUMAN_REJECT_LOCAL_ARCHIVE_RECONCILE"
        )
        if frozen_drive_resume:
            if (
                not attempt_evidence_path.is_file()
                or not provider_output_path.is_file()
            ):
                raise ValidationFailureError("MR1_DRIVE_RESUME_FROZEN_EVIDENCE_MISSING")
        else:
            _write_json_atomic(
                attempt_evidence_path,
                {
                    "schema_version": "mr1.provider-attempt-ledger-set.v1",
                    "run_id": state["run_id"],
                    "attempts": deepcopy(state["attempts"]),
                },
            )
            _write_json_atomic(
                provider_output_path,
                {
                    "schema_version": "mr1.provider-output-manifest.v1",
                    "run_id": state["run_id"],
                    "provider_call_counts": deepcopy(state["provider_call_counts"]),
                    "provider_outputs": deepcopy(state["provider_outputs"]),
                    "scene_executions": deepcopy(state["scene_executions"]),
                    "gemini_image_calls": 0,
                    "google_veo_calls": 0,
                    "youtube_calls": 0,
                },
            )

        local = deepcopy(state.get("local_result") or {})
        candidate_ref = deepcopy(state.get("review_media_candidate") or {})
        reuse_render_for_drive_resume = bool(
            local.get("state") == "READY_FOR_ARCHIVE"
            and candidate_ref.get("artifact_version_id")
            and candidate_ref.get("content_hash")
        )
        if reuse_render_for_drive_resume:
            # A resumable Drive repair must reuse the already-rendered bytes and
            # candidate receipt. Re-entering the local renderer here could change
            # the archive set after a remote mutation has already started.
            candidate_version = self._exact_version(
                uuid.UUID(candidate_ref["artifact_version_id"]),
                candidate_ref["content_hash"],
                CANDIDATE_ARTIFACT_TYPE,
                uuid.UUID(state["project_id"]),
            )
            drive_ledger = (state.get("attempts") or {}).get(
                "google_drive:archive",
                {},
            )
            if (
                drive_ledger.get("state") == "PLANNED"
                and drive_ledger.get("submit_state") == "NOT_SUBMITTED"
                and int(drive_ledger.get("attempt_count") or 0) == 0
                and drive_ledger.get("network_submit_started") is False
            ):
                # No remote mutation exists, so refresh service-generated
                # evidence that truthfully changed while recording the prior
                # pre-submit failure. Once mutation starts, the frozen archive
                # set below is reused byte-for-byte for reconciliation.
                local = self._with_service_archive_evidence(
                    local=local,
                    workspace=workspace,
                    state=state,
                )
                state["local_result"] = local
        else:
            if self.local_continuation is None:
                state["current_state"] = "REPAIRABLE_LOCAL_FAILURE"
                state["local_failure"] = "MR1_LOCAL_CONTINUATION_REQUIRED"
                run_version = self._save_run(run_artifact, state, actor_id=actor_id)
                return self._public_result(run_version, state)

            local = self.local_continuation.continue_once(
                run_id=uuid.UUID(state["run_id"]),
                workspace=workspace,
                authority=deepcopy(authority),
                provider_outputs=deepcopy(state["provider_outputs"]),
                resume_from=(
                    (state.get("local_result") or {}).get("resume_from")
                    if isinstance(state.get("local_result"), dict)
                    else state.get("local_result")
                ),
            )
            local = _jsonable(local)
            state["local_result"] = local
            if local.get("state") == "REPAIRABLE_LOCAL_FAILURE":
                state["current_state"] = "REPAIRABLE_LOCAL_FAILURE"
                state["repair_cycles"].append(
                    {
                        "stage": local.get("stage")
                        or local.get("failed_stage")
                        or "LOCAL_CONTINUATION",
                        "classification": local.get("classification")
                        or "DETERMINISTIC_LOCAL",
                        "reason": local.get("reason") or "UNKNOWN",
                        "provider_calls_repeated": False,
                        "recorded_at": datetime.now(UTC).isoformat(),
                    }
                )
                run_version = self._save_run(run_artifact, state, actor_id=actor_id)
                return self._public_result(run_version, state)
            if local.get("state") != "READY_FOR_ARCHIVE":
                raise ValidationFailureError("MR1_LOCAL_CONTINUATION_RESULT_INVALID")
            local_timeline = local.get("canonical_timeline") or {}
            if (
                local_timeline.get("content_hash")
                != state["temporal_authority"]["timeline_hash"]
                or local_timeline.get("estimated_timing_fallback_used") is not False
            ):
                state["current_state"] = "REPAIRABLE_LOCAL_FAILURE"
                state["local_result"] = {
                    **local,
                    "state": "REPAIRABLE_LOCAL_FAILURE",
                    "failed_stage": "CANONICAL_MEDIA_TIMELINE",
                    "resume_from": "CANONICAL_TIMELINE_READY",
                    "reason": "MR1_LOCAL_TIMELINE_CHANGED_AFTER_PEXELS",
                }
                state["repair_cycles"].append(
                    {
                        "stage": "CANONICAL_MEDIA_TIMELINE",
                        "classification": "DETERMINISTIC_LOCAL",
                        "reason": "MR1_LOCAL_TIMELINE_CHANGED_AFTER_PEXELS",
                        "provider_calls_repeated": False,
                        "recorded_at": datetime.now(UTC).isoformat(),
                    }
                )
                run_version = self._save_run(run_artifact, state, actor_id=actor_id)
                return self._public_result(run_version, state)
            candidate_content = deepcopy(local.get("review_media_candidate") or {})
            self._validate_candidate_payload(state, candidate_content)
            candidate_version = self._persist_candidate_once(
                state=state,
                candidate_content=candidate_content,
                actor_id=actor_id,
            )
            state["review_media_candidate"] = {
                **candidate_content,
                "artifact_version_id": str(candidate_version.id),
                "content_hash": candidate_version.content_hash,
            }
            technical_qc_version = self._persist_technical_qc_once(
                state=state,
                candidate_content=candidate_content,
                candidate_version=candidate_version,
                actor_id=actor_id,
            )
            state["technical_media_qc"] = {
                "artifact_version_id": str(technical_qc_version.id),
                "content_hash": technical_qc_version.content_hash,
                "review_round": int(state.get("review_round") or 1),
                "output_sha256": candidate_content["output_sha256"],
            }
            _write_json_atomic(
                execution_evidence / "review_media_candidate.json",
                deepcopy(state["review_media_candidate"]),
            )
            local = self._with_service_archive_evidence(
                local=local,
                workspace=workspace,
                state=state,
            )
            state["local_result"] = local
            state["render_attempts"] = int(
                local.get("render_attempts")
                or (local.get("native_ffmpeg_render") or {}).get("render_attempts")
                or 1
            )
            state["event_order"].extend(
                [
                    "ASSETS_NORMALIZED",
                    "NATIVE_RENDER_PLAN_PASS",
                    "NATIVE_MOTION_COMPILER_PASS",
                    "NATIVE_FFMPEG_RENDER_PASS",
                    "TECHNICAL_MEDIA_QC_PASS",
                    "CREATIVE_MEDIA_QC_REVIEW_REQUIRED",
                    "REVIEW_MEDIA_CANDIDATE_CREATED",
                ]
            )
            state["current_state"] = "READY_FOR_ARCHIVE"
            run_version = self._save_run(run_artifact, state, actor_id=actor_id)

        self._require_technical_qc_authority(
            state=state,
            candidate=candidate_version,
            candidate_content=deepcopy(candidate_version.content or {}),
        )

        if state.get("drive_archive") is None:
            drive_ok, drive_output, run_version = self._execute_drive(
                run_artifact=run_artifact,
                run_version=run_version,
                state=state,
                actor_id=actor_id,
                gateways=gateways,
                local=local,
            )
            if not drive_ok:
                return self._public_result(run_version, state)
            drive_version = self._persist_drive_receipt_once(
                state=state,
                receipt=drive_output,
                candidate_version=candidate_version,
                actor_id=actor_id,
            )
            state["drive_archive"] = {
                **drive_output,
                "artifact_version_id": str(drive_version.id),
                "content_hash": drive_version.content_hash,
            }
        final_review_task = self._ensure_final_human_review_task(
            state=state,
            candidate_version=candidate_version,
            requested_by_user_id=actor_id,
        )
        state["final_human_review_task_id"] = str(final_review_task.id)
        state["final_human_review_assigned_to_user_id"] = str(
            final_review_task.assigned_to_user_id
        )
        state["current_state"] = "AWAITING_HUMAN_FULL_WATCH"
        state["state"] = "AWAITING_HUMAN_FULL_WATCH"
        state["event_order"].extend(
            ["DRIVE_ARCHIVE_VERIFIED", "HUMAN_FULL_WATCH_PENDING"]
        )
        run_version = self._save_run(run_artifact, state, actor_id=actor_id)
        self._durable_boundary()
        return self._public_result(run_version, state)

    @staticmethod
    def _freeze_candidate_authority_bindings(
        authority: dict[str, Any],
    ) -> dict[str, Any]:
        resolved = authority.get("resolved") or {}

        def ref(key: str) -> dict[str, Any]:
            value = resolved.get(key) or {}
            required = {
                "artifact_id",
                "artifact_version_id",
                "artifact_version_ref",
                "version_number",
                "content_hash",
            }
            if not required.issubset(value):
                raise ValidationFailureError(f"MR1_CANDIDATE_AUTHORITY_MISSING:{key}")
            return {name: deepcopy(value[name]) for name in sorted(required)}

        exact_bindings = authority.get("exact_bindings") or {}
        bindings = {
            "schema_version": "mr1.candidate-authority-bindings.v1",
            "package": {
                "artifact_version_id": authority["package_artifact_version_id"],
                "content_hash": authority["package_content_hash"],
            },
            "approval": {
                "approval_decision_id": authority["approval_id"],
                "approval_content_hash": authority["approval_content_hash"],
            },
            "channel_profile_version": deepcopy(
                exact_bindings["channel_profile_version"]
            ),
            "compiled_channel_policy_snapshot": deepcopy(
                exact_bindings["compiled_channel_policy_snapshot"]
            ),
            "rights_disclosure_completeness_report": ref(
                "rights_disclosure_completeness_report"
            ),
            "synthetic_media_disclosure_receipt_draft": ref(
                "synthetic_media_disclosure_receipt_draft"
            ),
            "asset_provenance_plan": ref("asset_provenance_plan"),
            "visual_plan": ref("visual_plan"),
            "provider_execution_plan": ref("provider_execution_plan"),
        }
        if resolved.get("supplemental_visual_alignment"):
            bindings["supplemental_visual_alignment"] = ref(
                "supplemental_visual_alignment"
            )
        bindings["content_hash"] = content_hash(bindings)
        return bindings

    @staticmethod
    def _freeze_final_media_lineage_authority(
        authority: dict[str, Any],
    ) -> dict[str, Any]:
        resolved = authority.get("resolved") or {}

        def exact_artifact_ref(key: str) -> dict[str, Any]:
            value = resolved.get(key) or {}
            required = {
                "artifact_id",
                "artifact_version_id",
                "artifact_version_ref",
                "version_number",
                "content_hash",
            }
            if not required.issubset(value):
                raise ValidationFailureError(
                    f"MR1_FINAL_LINEAGE_AUTHORITY_MISSING:{key}"
                )
            return {name: deepcopy(value[name]) for name in sorted(required)}

        exact_bindings = authority.get("exact_bindings") or {}
        profile = exact_bindings.get("channel_profile_version") or {}
        snapshot = exact_bindings.get("compiled_channel_policy_snapshot") or {}
        if not profile.get("id") or not profile.get("content_hash"):
            raise ValidationFailureError("MR1_FINAL_LINEAGE_PROFILE_AUTHORITY_MISSING")
        if not snapshot.get("id") or not snapshot.get("content_hash"):
            raise ValidationFailureError("MR1_FINAL_LINEAGE_SNAPSHOT_AUTHORITY_MISSING")
        frozen = {
            "schema_version": "mr1.final-media-lineage-authority.v1",
            "package": {
                "artifact_version_id": authority["package_artifact_version_id"],
                "content_hash": authority["package_content_hash"],
            },
            "approval": {
                "approval_decision_id": authority["approval_id"],
                "approval_content_hash": authority["approval_content_hash"],
            },
            "channel_profile_version": deepcopy(profile),
            "compiled_channel_policy_snapshot": deepcopy(snapshot),
            "target_market_profile": exact_artifact_ref("target_market_profile"),
            "target_market_digest": exact_artifact_ref("target_market_digest"),
            "market_alignment_dossier": exact_artifact_ref("market_alignment_dossier"),
            "niche_alignment_dossier": exact_artifact_ref("niche_alignment_dossier"),
        }
        if resolved.get("supplemental_visual_alignment"):
            frozen["supplemental_visual_alignment"] = exact_artifact_ref(
                "supplemental_visual_alignment"
            )
        frozen["content_hash"] = content_hash(frozen)
        return frozen

    @staticmethod
    def _narration_runtime_gate(
        authority: dict[str, Any], output: dict[str, Any]
    ) -> dict[str, Any]:
        pacing = authority["resolved"]["narration_pacing_preflight_estimate"]["content"]
        limits = pacing.get("target_runtime_minutes") or {}
        minimum_ms = round(float(limits.get("minimum") or 0) * 60_000)
        maximum_ms = round(float(limits.get("maximum") or 0) * 60_000)
        duration_ms = int(
            output.get("audio_duration_ms") or output.get("duration_ms") or 0
        )
        exact_limits = minimum_ms == 360_000 and maximum_ms == 720_000
        passed = bool(
            exact_limits
            and minimum_ms <= duration_ms <= maximum_ms
            and output.get("provider_call_made") is True
        )
        evidence = {
            "schema_version": "mr1.narration-runtime-hard-gate.v1",
            "timing_source": "ACTUAL_PROVIDER_AUDIO_DURATION",
            "pacing_artifact_version_id": authority["resolved"][
                "narration_pacing_preflight_estimate"
            ]["artifact_version_id"],
            "pacing_artifact_content_hash": authority["resolved"][
                "narration_pacing_preflight_estimate"
            ]["content_hash"],
            "minimum_duration_ms": minimum_ms,
            "maximum_duration_ms": maximum_ms,
            "actual_duration_ms": duration_ms,
            "inclusive_boundaries": True,
            "alignment_calls_before_gate": 0,
            "pexels_calls_before_gate": 0,
            "result": "PASS" if passed else "BLOCK",
            "reason_codes": (
                [] if passed else ["NARRATION_RUNTIME_OUTSIDE_APPROVED_HARD_LIMITS"]
            ),
        }
        evidence["content_hash"] = content_hash(evidence)
        return evidence

    def _with_service_archive_evidence(
        self,
        *,
        local: dict[str, Any],
        workspace: Path,
        state: dict[str, Any],
    ) -> dict[str, Any]:
        sources = deepcopy(
            local.get("archive_sources")
            or local.get("archive_items")
            or local.get("files")
            or []
        )
        if not isinstance(sources, list):
            raise ValidationFailureError("MR1_LOCAL_ARCHIVE_SOURCES_INVALID")
        existing_sources_by_path = {
            str(
                Path(
                    str(
                        item.get("source_path")
                        or item.get("local_path")
                        or item.get("path")
                        or ""
                    )
                ).resolve()
            ): item
            for item in sources
            if isinstance(item, dict)
        }
        existing_paths = set(existing_sources_by_path)
        existing_roles = {
            str(item.get("logical_role") or item.get("role") or "").casefold()
            for item in sources
            if isinstance(item, dict)
        }
        required = [
            ("MR1_SERVICE_AUTHORITY", workspace / "authority.json"),
            ("MR1_SERVICE_MASTER_PREFLIGHT", workspace / "master_preflight.json"),
            ("MR1_SERVICE_TASK_AUTHORIZATION", workspace / "task_authorization.json"),
            (
                "MR1_SERVICE_PROVIDER_ATTEMPT_LEDGERS",
                workspace / "execution_evidence" / "provider_attempt_ledgers.json",
            ),
            (
                "MR1_SERVICE_PROVIDER_OUTPUT_MANIFEST",
                workspace / "execution_evidence" / "provider_output_manifest.json",
            ),
            (
                "MR1_SERVICE_MONTHLY_BUDGET_RESERVATION",
                workspace / "execution_evidence" / "monthly_budget_reservation.json",
            ),
            (
                "MR1_SERVICE_REVIEW_MEDIA_CANDIDATE",
                workspace / "execution_evidence" / "review_media_candidate.json",
            ),
        ]
        safe_evidence_index = 0
        for operation_key, attempt in sorted(
            (state.get("attempts") or {}).items()
        ):
            safe_failure_evidence = (
                attempt.get("safe_failure_evidence")
                if isinstance(attempt, dict)
                else None
            )
            if not isinstance(safe_failure_evidence, dict):
                continue
            if not self._sanitized_pexels_failure_evidence_ref_exact(
                safe_failure_evidence=safe_failure_evidence,
                workspace=workspace,
            ):
                raise ValidationFailureError(
                    "MR1_PEXELS_SAFE_FAILURE_EVIDENCE_CHANGED"
                )
            safe_evidence_index += 1
            evidence_relative = PurePosixPath(
                safe_failure_evidence["evidence_ref"].removeprefix(
                    "workspace-relative://"
                )
            )
            required.append(
                (
                    "MR1_PEXELS_SAFE_FAILURE_EVIDENCE_"
                    f"{safe_evidence_index:02d}_{operation_key.upper()}",
                    workspace.joinpath(*evidence_relative.parts),
                )
            )
        continuation_approval = (
            workspace / "provider_attempt_continuation_approval.json"
        )
        if continuation_approval.is_file():
            required.append(
                (
                    "MR1_PROVIDER_ATTEMPT_CONTINUATION_APPROVAL",
                    continuation_approval,
                )
            )
        required.extend(
            (
                f"MR1_RUNTIME_SUBMIT_PREFLIGHT_{index:02d}",
                path,
            )
            for index, path in enumerate(
                sorted((workspace / "runtime_submit_preflights").glob("*.json")),
                start=1,
            )
        )
        required.extend(
            (
                f"MR1_REUSE_EVIDENCE_{index:02d}",
                path,
            )
            for index, path in enumerate(
                sorted((workspace / "reuse_evidence").glob("*.json")),
                start=1,
            )
        )
        human_repair_directive = workspace / "human_repair_directive.json"
        if human_repair_directive.is_file():
            required.append(("MR1_HUMAN_REPAIR_DIRECTIVE", human_repair_directive))
        for role, path in required:
            resolved = path.resolve(strict=True)
            if workspace != resolved and workspace not in resolved.parents:
                raise ValidationFailureError("MR1_ARCHIVE_EVIDENCE_PATH_ESCAPE")
            if str(resolved) in existing_paths:
                existing = existing_sources_by_path[str(resolved)]
                existing_role = str(
                    existing.get("logical_role")
                    or existing.get("role")
                    or ""
                ).strip()
                if existing_role.casefold() != role.casefold():
                    raise ValidationFailureError(
                        "MR1_ARCHIVE_EVIDENCE_ROLE_CONFLICT"
                    )
                # Service evidence may legitimately change after a purely
                # pre-submit failure (for example the attempt-ledger snapshot).
                # Refresh its local manifest metadata before any Drive mutation;
                # post-submit resumable flows keep these files frozen above.
                existing["source_path"] = str(resolved)
                existing["sha256"] = _sha256_file(resolved)
                existing["size_bytes"] = resolved.stat().st_size
                continue
            if role.casefold() in existing_roles:
                raise ValidationFailureError("MR1_ARCHIVE_EVIDENCE_ROLE_CONFLICT")
            source = {
                "logical_role": role,
                "source_path": str(resolved),
                "sha256": _sha256_file(resolved),
                "size_bytes": resolved.stat().st_size,
            }
            sources.append(source)
            existing_paths.add(str(resolved))
            existing_sources_by_path[str(resolved)] = source
            existing_roles.add(role.casefold())
        result = deepcopy(local)
        result["archive_sources"] = sources
        return result

    def _execute_one_shot(
        self,
        *,
        run_artifact: Artifact,
        state: dict[str, Any],
        run_version: ArtifactVersion,
        actor_id: uuid.UUID,
        operation_key: str,
        event: str,
        counter: str,
        request: dict[str, Any],
        invoke: Callable[[Callable[[], None]], Any],
    ) -> tuple[bool, dict[str, Any] | None, ArtifactVersion]:
        ledger = state["attempts"][operation_key]
        if ledger["state"] == "SUCCEEDED":
            return True, deepcopy(ledger.get("output")), run_version
        if ledger["attempt_count"] >= ledger["attempt_cap"]:
            state["current_state"] = "BLOCKED_REQUIRES_NEW_MR1_APPROVAL"
            run_version = self._save_run(run_artifact, state, actor_id=actor_id)
            return False, None, run_version
        submitted = False

        def before_submit() -> None:
            nonlocal submitted, run_version
            if submitted or ledger["attempt_count"] >= ledger["attempt_cap"]:
                raise RuntimeError("MR1_DUPLICATE_PROVIDER_SUBMIT_BLOCKED")
            self._runtime_submit_preflight(state, operation_key, request)
            self._mark_budget_submitted_if_needed(state)
            submitted = True
            ledger["attempt_count"] += 1
            ledger["network_submit_started"] = True
            ledger["state"] = "SUBMITTING"
            ledger["submit_state"] = "SUBMITTING"
            ledger["request_hash"] = request["request_hash"]
            ledger["submitted_at"] = datetime.now(UTC).isoformat()
            state["provider_call_counts"][counter] += 1
            state["provider_call_counts"]["logical_total"] += 1
            self._save_attempt(state, operation_key, actor_id)
            state["event_order"].append(f"{event}_SUBMITTING")
            run_version = self._save_run(run_artifact, state, actor_id=actor_id)
            self._durable_boundary()

        try:
            output = _redact_volatile(_jsonable(invoke(before_submit)))
            if not submitted:
                raise RuntimeError("MR1_PROVIDER_GATEWAY_SUBMIT_BOUNDARY_NOT_DECLARED")
            if not isinstance(output, dict) or not output:
                raise RuntimeError("MR1_PROVIDER_OUTPUT_INVALID")
        except Exception as exc:
            if submitted:
                self._settle_budget_consumed_failure(state)
                ledger["state"] = "CONSUMED_FAILED"
                ledger["submit_state"] = "FAILED_CONSUMED"
                ledger["failure"] = f"{type(exc).__name__}:{exc}"
                state["current_state"] = "BLOCKED_REQUIRES_NEW_MR1_APPROVAL"
                state["blocker"] = f"{operation_key}:POST_SUBMIT_FAILURE"
            else:
                ledger["state"] = "PLANNED"
                ledger["submit_state"] = "NOT_SUBMITTED"
                ledger["pre_submit_failures"] += 1
                ledger["last_pre_submit_failure"] = f"{type(exc).__name__}:{exc}"
                state["current_state"] = "BLOCKED_PRE_SUBMIT_REPAIRABLE"
                state["blocker"] = f"{operation_key}:PRE_SUBMIT_REPAIRABLE"
            self._save_attempt(state, operation_key, actor_id)
            run_version = self._save_run(run_artifact, state, actor_id=actor_id)
            return False, None, run_version

        ledger["state"] = "SUCCEEDED"
        ledger["submit_state"] = "SUCCEEDED"
        ledger["output"] = output
        ledger["succeeded_at"] = datetime.now(UTC).isoformat()
        self._save_attempt(state, operation_key, actor_id)
        state["event_order"].append(f"{event}_PASS")
        run_version = self._save_run(run_artifact, state, actor_id=actor_id)
        return True, output, run_version

    def _execute_pexels_once(
        self,
        *,
        run_artifact: Artifact,
        state: dict[str, Any],
        run_version: ArtifactVersion,
        actor_id: uuid.UUID,
        scene_id: str,
        operation_key: str,
        request: dict[str, Any],
        invoke: Callable[[Callable[[], None], Callable[[], None]], Any],
    ) -> tuple[bool, dict[str, Any] | None, ArtifactVersion]:
        ledger = state["attempts"][operation_key]
        if ledger["state"] == "SUCCEEDED":
            return True, deepcopy(ledger.get("output")), run_version
        if ledger["attempt_count"] >= ledger["attempt_cap"]:
            state["current_state"] = "BLOCKED_REQUIRES_NEW_MR1_APPROVAL"
            run_version = self._save_run(run_artifact, state, actor_id=actor_id)
            return False, None, run_version
        search_submitted = False
        download_submitted = False
        attempt_ordinal: int | None = None

        def before_search() -> None:
            nonlocal search_submitted, run_version, attempt_ordinal
            if search_submitted or ledger["attempt_count"] >= ledger["attempt_cap"]:
                raise RuntimeError("MR1_DUPLICATE_PEXELS_SEARCH_BLOCKED")
            self._runtime_submit_preflight(state, operation_key, request)
            self._mark_budget_submitted_if_needed(state)
            search_submitted = True
            attempt_ordinal = int(ledger["attempt_count"]) + 1
            ledger["attempt_count"] = attempt_ordinal
            ledger["search_submit_count"] = (
                int(ledger.get("search_submit_count") or 0) + 1
            )
            ledger["network_submit_started"] = True
            ledger["state"] = "SEARCH_SUBMITTING"
            ledger["submit_state"] = "SUBMITTING"
            ledger["request_hash"] = request["request_hash"]
            ledger["active_attempt_ordinal"] = attempt_ordinal
            state["provider_call_counts"]["pexels"] += 1
            state["provider_call_counts"]["logical_total"] += 1
            self._save_attempt(state, operation_key, actor_id)
            state["event_order"].append(f"PEXELS_{scene_id}_SEARCH_SUBMITTING")
            run_version = self._save_run(run_artifact, state, actor_id=actor_id)
            self._durable_boundary()

        def before_download() -> None:
            nonlocal download_submitted, run_version
            if not search_submitted or download_submitted:
                raise RuntimeError("MR1_PEXELS_DOWNLOAD_BOUNDARY_INVALID")
            download_submitted = True
            ledger["download_submit_count"] = (
                int(ledger.get("download_submit_count") or 0) + 1
            )
            ledger["state"] = "DOWNLOAD_SUBMITTING"
            self._save_attempt(state, operation_key, actor_id)
            state["event_order"].append(f"PEXELS_{scene_id}_DOWNLOAD_SUBMITTING")
            run_version = self._save_run(run_artifact, state, actor_id=actor_id)
            self._durable_boundary()

        try:
            output = _redact_volatile(_jsonable(invoke(before_search, before_download)))
            if not search_submitted or not download_submitted:
                raise RuntimeError("MR1_PEXELS_SUBMIT_BOUNDARY_INCOMPLETE")
            if not isinstance(output, dict) or not output:
                raise RuntimeError("MR1_PEXELS_OUTPUT_INVALID")
            if output.get("route") not in {None, "PEXELS_VIDEO"}:
                raise RuntimeError("MR1_PEXELS_ROUTE_CHANGED")
        except Exception as exc:
            if search_submitted:
                self._settle_budget_consumed_failure(state)
                ledger["state"] = "CONSUMED_FAILED"
                ledger["submit_state"] = "FAILED_CONSUMED"
                ledger["failure"] = f"{type(exc).__name__}:{exc}"
                safe_failure_evidence = self._sanitized_pexels_failure_evidence(
                    exc=exc,
                    workspace=Path(state["workspace"]),
                )
                outcome = {
                    "attempt_ordinal": attempt_ordinal,
                    "state": "CONSUMED_FAILED",
                    "request_hash": request["request_hash"],
                    "failure": ledger["failure"],
                    "recorded_at": datetime.now(UTC).isoformat(),
                }
                if safe_failure_evidence is not None:
                    ledger["safe_failure_evidence"] = deepcopy(
                        safe_failure_evidence
                    )
                    outcome["safe_failure_evidence_ref"] = {
                        key: deepcopy(safe_failure_evidence[key])
                        for key in (
                            "safe_evidence_kind",
                            "provider_evidence_schema_version",
                            "guarded_key",
                            "reason_code",
                            "provider_evidence_content_hash",
                            "evidence_ref",
                            "evidence_file_sha256",
                            "content_hash",
                        )
                    }
                ledger.setdefault("attempt_outcomes", []).append(outcome)
                state["current_state"] = "BLOCKED_REQUIRES_NEW_MR1_APPROVAL"
                state["blocker"] = f"{operation_key}:POST_SUBMIT_FAILURE"
            else:
                ledger["state"] = "PLANNED"
                ledger["submit_state"] = "NOT_SUBMITTED"
                ledger["pre_submit_failures"] += 1
                ledger["last_pre_submit_failure"] = f"{type(exc).__name__}:{exc}"
                state["current_state"] = "BLOCKED_PRE_SUBMIT_REPAIRABLE"
                state["blocker"] = f"{operation_key}:PRE_SUBMIT_REPAIRABLE"
            self._save_attempt(state, operation_key, actor_id)
            run_version = self._save_run(run_artifact, state, actor_id=actor_id)
            return False, None, run_version

        ledger["state"] = "SUCCEEDED"
        ledger["submit_state"] = "SUCCEEDED"
        ledger["output"] = output
        ledger["succeeded_at"] = datetime.now(UTC).isoformat()
        ledger.setdefault("attempt_outcomes", []).append(
            {
                "attempt_ordinal": attempt_ordinal,
                "state": "SUCCEEDED",
                "request_hash": request["request_hash"],
                "output_sha256": output.get("sha256"),
                "recorded_at": ledger["succeeded_at"],
            }
        )
        self._save_attempt(state, operation_key, actor_id)
        state["event_order"].append(f"PEXELS_{scene_id}_PASS")
        run_version = self._save_run(run_artifact, state, actor_id=actor_id)
        return True, output, run_version

    def _execute_drive(
        self,
        *,
        run_artifact: Artifact,
        run_version: ArtifactVersion,
        state: dict[str, Any],
        actor_id: uuid.UUID,
        gateways: MR1ProviderGateways,
        local: dict[str, Any],
    ) -> tuple[bool, dict[str, Any] | None, ArtifactVersion]:
        key = "google_drive:archive"
        ledger = state["attempts"][key]
        safe_evidence_bindings: list[tuple[Path, str]] = []
        for attempt in (state.get("attempts") or {}).values():
            safe_failure_evidence = (
                attempt.get("safe_failure_evidence")
                if isinstance(attempt, dict)
                else None
            )
            if isinstance(
                safe_failure_evidence, dict
            ) and not self._sanitized_pexels_failure_evidence_ref_exact(
                safe_failure_evidence=safe_failure_evidence,
                workspace=Path(state["workspace"]),
            ):
                raise ValidationFailureError(
                    "MR1_PEXELS_SAFE_FAILURE_EVIDENCE_CHANGED"
                )
            if isinstance(safe_failure_evidence, dict):
                evidence_relative = PurePosixPath(
                    safe_failure_evidence["evidence_ref"].removeprefix(
                        "workspace-relative://"
                    )
                )
                safe_evidence_bindings.append(
                    (
                        Path(state["workspace"])
                        .joinpath(*evidence_relative.parts)
                        .resolve(strict=True),
                        safe_failure_evidence["evidence_file_sha256"],
                    )
                )
        authorized_phases = (state.get("task_authorization") or {}).get(
            "drive_idempotency_phases"
        ) or []
        if authorized_phases and (
            len(authorized_phases) != 2
            or ledger.get("drive_phase_authority") != authorized_phases[0]
            or ledger.get("distinct_from_finalization_supplement") is not True
            or (state.get("attempts") or {})
            .get(MR1_DRIVE_FINALIZATION_OPERATION_KEY, {})
            .get("drive_phase_authority")
            != authorized_phases[1]
        ):
            raise ValidationFailureError("MR1_CANONICAL_DRIVE_PHASE_AUTHORITY_INVALID")
        mutation_started = False
        archive_sources = deepcopy(
            local.get("archive_sources")
            or local.get("archive_items")
            or local.get("files")
            or []
        )
        for evidence_path, expected_sha256 in safe_evidence_bindings:
            matching_sources = [
                item
                for item in archive_sources
                if isinstance(item, dict)
                and Path(
                    str(
                        item.get("source_path")
                        or item.get("local_path")
                        or item.get("path")
                        or ""
                    )
                ).resolve()
                == evidence_path
            ]
            if (
                len(matching_sources) != 1
                or matching_sources[0].get("sha256")
                != expected_sha256
            ):
                raise ValidationFailureError(
                    "MR1_PEXELS_SAFE_FAILURE_ARCHIVE_BINDING_INVALID"
                )
        manifest = self._finalize_drive_request_manifest(
            state=state,
            archive_sources=archive_sources,
        )
        state["final_drive_request_manifest"] = {
            "path": manifest["final_manifest_path"],
            "content_hash": manifest["final_manifest_content_hash"],
            "item_set_hash": manifest["item_set_hash"],
            "item_count": manifest["item_count"],
            "review_round": manifest["review_round"],
        }
        request_manifest = {
            "schema_version": "mr1.local-archive-manifest.v1",
            "run_id": state["run_id"],
            "archive_identity": state["archive_identity"],
            "review_media_candidate": deepcopy(state["review_media_candidate"]),
            "review_round": manifest["review_round"],
            "item_count": manifest["item_count"],
            "total_size_bytes": manifest["total_size_bytes"],
            "item_set_hash": manifest["item_set_hash"],
            "files": deepcopy(manifest["files"]),
        }
        request_core = {
            "provider": "google_drive",
            "operation": "archive",
            "archive_identity": state["archive_identity"],
            "manifest_hash": content_hash(request_manifest),
            "final_manifest_content_hash": manifest["final_manifest_content_hash"],
            "item_set_hash": manifest["item_set_hash"],
            "idempotency_key": ledger["idempotency_key"],
            "idempotency_fingerprint": ledger["idempotency_fingerprint"],
            "sdk_retry": False,
        }
        drive_request = {
            **request_core,
            "request_hash": content_hash(request_core),
        }

        def before_first_mutation() -> None:
            nonlocal mutation_started, run_version
            if mutation_started:
                raise RuntimeError("MR1_DUPLICATE_DRIVE_MUTATION_BOUNDARY")
            self._runtime_submit_preflight(state, key, drive_request)
            self._mark_budget_submitted_if_needed(state)
            mutation_started = True
            attempt_count = int(ledger.get("attempt_count") or 0)
            if attempt_count == 0:
                if (
                    ledger.get("state") != "PLANNED"
                    or ledger.get("network_submit_started") is not False
                ):
                    raise RuntimeError("MR1_DRIVE_ATTEMPT_LEDGER_CONFLICT")
                ledger["attempt_count"] = 1
                ledger["network_submit_started"] = True
                state["provider_call_counts"]["drive"] = (
                    int(state["provider_call_counts"].get("drive") or 0) + 1
                )
                state["provider_call_counts"]["logical_total"] = (
                    int(
                        state["provider_call_counts"].get("logical_total")
                        or 0
                    )
                    + 1
                )
            elif (
                attempt_count != 1
                or ledger.get("state") != "RESUMABLE_FAILURE"
                or ledger.get("network_submit_started") is not True
            ):
                raise RuntimeError("MR1_DRIVE_ATTEMPT_LEDGER_CONFLICT")
            ledger["state"] = "MUTATING_RESUMABLE_ARCHIVE"
            ledger["submit_state"] = "SUBMITTING_OR_RECONCILING"
            self._save_attempt(state, key, actor_id)
            state["event_order"].append("DRIVE_ARCHIVE_MUTATION_STARTED")
            run_version = self._save_run(run_artifact, state, actor_id=actor_id)
            self._durable_boundary()

        try:
            receipt = _redact_volatile(
                _jsonable(
                    gateways.drive.upload_or_resume_and_verify(
                        manifest=request_manifest,
                        archive_identity=state["archive_identity"],
                        journal_path=Path(state["workspace"])
                        / "drive"
                        / "remote-id-journal.json",
                        before_first_mutation=before_first_mutation,
                    )
                )
            )
            if not mutation_started or not isinstance(receipt, dict):
                raise RuntimeError("MR1_DRIVE_MUTATION_BOUNDARY_NOT_DECLARED")
            verification = self._validate_drive_receipt_proof(
                state=state,
                request_manifest=request_manifest,
                final_manifest=manifest,
                receipt=receipt,
            )
            receipt = {
                **receipt,
                "review_round": manifest["review_round"],
                "review_media_candidate_artifact_version_id": (
                    state["review_media_candidate"]["artifact_version_id"]
                ),
                "review_media_candidate_content_hash": (
                    state["review_media_candidate"]["content_hash"]
                ),
                "final_drive_request_manifest_path": manifest["final_manifest_path"],
                "final_drive_request_manifest_hash": manifest[
                    "final_manifest_content_hash"
                ],
                "request_manifest_hash": content_hash(request_manifest),
                "item_set_hash": manifest["item_set_hash"],
                "verification": verification,
            }
        except Exception as exc:
            ledger["failure"] = f"{type(exc).__name__}:{exc}"
            resumable_flow_exists = bool(
                mutation_started
                or (
                    int(ledger.get("attempt_count") or 0) == 1
                    and ledger.get("network_submit_started") is True
                )
            )
            if resumable_flow_exists:
                ledger["state"] = "RESUMABLE_FAILURE"
                ledger["submit_state"] = "RESUMABLE_FAILURE"
                state["current_state"] = "REPAIRABLE_DRIVE_FAILURE"
            else:
                ledger["state"] = "PLANNED"
                ledger["submit_state"] = "NOT_SUBMITTED"
                ledger["pre_submit_failures"] += 1
                state["current_state"] = "BLOCKED_PRE_SUBMIT_REPAIRABLE"
            self._save_attempt(state, key, actor_id)
            run_version = self._save_run(run_artifact, state, actor_id=actor_id)
            return False, None, run_version
        ledger["state"] = "SUCCEEDED"
        ledger["submit_state"] = "SUCCEEDED"
        ledger["output"] = receipt
        self._save_attempt(state, key, actor_id)
        run_version = self._save_run(run_artifact, state, actor_id=actor_id)
        return True, receipt, run_version

    def _finalize_drive_request_manifest(
        self,
        *,
        state: dict[str, Any],
        archive_sources: list[Any],
    ) -> dict[str, Any]:
        """Freeze the exact Drive request set after all service evidence exists.

        This manifest is detached from the uploaded set, avoiding a self-hash
        cycle while still proving that its ``files`` value is byte-for-byte the
        same value handed to the Drive gateway.
        """

        if not isinstance(archive_sources, list) or not archive_sources:
            raise ValidationFailureError("MR1_DRIVE_ARCHIVE_SOURCES_EMPTY")
        workspace = Path(state["workspace"]).resolve(strict=True)
        canonical: list[dict[str, Any]] = []
        roles: set[str] = set()
        paths: set[str] = set()
        archive_paths: set[str] = set()
        names: set[str] = set()
        for index, raw in enumerate(archive_sources, start=1):
            if not isinstance(raw, dict):
                raise ValidationFailureError("MR1_DRIVE_ARCHIVE_SOURCE_INVALID")
            role = str(raw.get("logical_role") or raw.get("role") or "").strip()
            source_value = (
                raw.get("source_path") or raw.get("local_path") or raw.get("path")
            )
            unresolved = Path(str(source_value or ""))
            if unresolved.is_symlink():
                raise ValidationFailureError("MR1_DRIVE_ARCHIVE_SYMLINK_FORBIDDEN")
            try:
                source = unresolved.resolve(strict=True)
            except (FileNotFoundError, OSError) as exc:
                raise ValidationFailureError(
                    "MR1_DRIVE_ARCHIVE_SOURCE_MISSING"
                ) from exc
            role_key = role.casefold()
            if not role or role_key in roles:
                raise ValidationFailureError(
                    "MR1_DRIVE_ARCHIVE_ROLE_INVALID_OR_DUPLICATE"
                )
            if workspace != source and workspace not in source.parents:
                raise ValidationFailureError("MR1_DRIVE_ARCHIVE_SOURCE_PATH_ESCAPE")
            if not source.is_file() or str(source) in paths:
                raise ValidationFailureError(
                    "MR1_DRIVE_ARCHIVE_SOURCE_INVALID_OR_DUPLICATE"
                )
            actual_sha256 = _sha256_file(source)
            actual_size = source.stat().st_size
            if raw.get("sha256") not in {None, actual_sha256}:
                raise ValidationFailureError("MR1_DRIVE_ARCHIVE_SOURCE_HASH_CHANGED")
            if raw.get("size_bytes") not in {None, actual_size}:
                raise ValidationFailureError("MR1_DRIVE_ARCHIVE_SOURCE_SIZE_CHANGED")
            role_component = re.sub(
                r"[^a-z0-9._-]+", "-", role.strip().casefold()
            ).strip("-._")[:80]
            if not role_component:
                raise ValidationFailureError("MR1_DRIVE_ARCHIVE_ROLE_COMPONENT_INVALID")
            requested_archive_path = raw.get("archive_path") or raw.get(
                "expected_archive_path"
            )
            archive_path = (
                str(requested_archive_path)
                if requested_archive_path
                else (
                    f"items/{role_component}/{index:03d}-{role_component}-{source.name}"
                )
            )
            posix_path = PurePosixPath(archive_path)
            name = posix_path.name
            if (
                not archive_path
                or archive_path.startswith("/")
                or "\\" in archive_path
                or str(posix_path) != archive_path
                or any(part in {"", ".", ".."} for part in posix_path.parts)
                or not name
                or archive_path.casefold() in archive_paths
                or name.casefold() in names
            ):
                raise ValidationFailureError(
                    "MR1_DRIVE_ARCHIVE_PATH_OR_NAME_INVALID_OR_DUPLICATE"
                )
            item = {
                "logical_role": role,
                "name": name,
                "source_path": str(source),
                "archive_path": archive_path,
                "sha256": actual_sha256,
                "md5": _md5_file(source),
                "size_bytes": actual_size,
            }
            canonical.append(item)
            roles.add(role_key)
            paths.add(str(source))
            archive_paths.add(archive_path.casefold())
            names.add(name.casefold())

        item_set_hash = content_hash({"files": canonical})
        review_round = int(state.get("review_round") or 1)
        final_manifest = {
            "schema_version": "mr1.final-drive-request-manifest.v1",
            "run_id": state["run_id"],
            "archive_identity": state["archive_identity"],
            "review_round": review_round,
            "review_media_candidate": deepcopy(state["review_media_candidate"]),
            "item_count": len(canonical),
            "total_size_bytes": sum(item["size_bytes"] for item in canonical),
            "item_set_hash": item_set_hash,
            "files": canonical,
            "detached_manifest_not_uploaded": True,
            "self_hash_cycle_avoided": True,
        }
        manifest_hash = content_hash(final_manifest)
        path = (
            workspace
            / "execution_evidence"
            / f"final-drive-request-manifest-r{review_round:02d}.json"
        )
        if path.exists():
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ValidationFailureError(
                    "MR1_FINAL_DRIVE_MANIFEST_UNREADABLE"
                ) from exc
            if existing != final_manifest:
                drive_ledger = (state.get("attempts") or {}).get(
                    "google_drive:archive",
                    {},
                )
                if not (
                    drive_ledger.get("state") == "PLANNED"
                    and drive_ledger.get("submit_state") == "NOT_SUBMITTED"
                    and int(drive_ledger.get("attempt_count") or 0) == 0
                    and drive_ledger.get("network_submit_started") is False
                ):
                    raise ValidationFailureError(
                        "MR1_FINAL_DRIVE_MANIFEST_IDEMPOTENCY_CONFLICT"
                    )
                # A failed gateway invocation before its declared mutation
                # callback has no remote identity to reconcile. Re-freeze the
                # service evidence updated by that failure; after the first
                # mutation boundary this path remains strictly immutable.
                _write_json_atomic(path, final_manifest)
        else:
            _write_json_atomic(path, final_manifest)
        if _sha256_file(path) != _sha256_json_file_payload(final_manifest):
            raise ValidationFailureError("MR1_FINAL_DRIVE_MANIFEST_WRITE_MISMATCH")
        return {
            **final_manifest,
            "final_manifest_path": str(path.resolve(strict=True)),
            "final_manifest_content_hash": manifest_hash,
        }

    def _validate_drive_receipt_proof(
        self,
        *,
        state: dict[str, Any],
        request_manifest: dict[str, Any],
        final_manifest: dict[str, Any],
        receipt: dict[str, Any],
    ) -> dict[str, bool]:
        """Independently prove every Drive archive invariant, fail closed."""

        expected_files = request_manifest["files"]
        expected_count = len(expected_files)
        items = receipt.get("items")
        files = receipt.get("files")
        if not isinstance(items, list) or not isinstance(files, list):
            raise RuntimeError("MR1_DRIVE_ITEM_PROOF_MISSING")
        expected_by_role = {
            item["logical_role"].casefold(): item for item in expected_files
        }
        items_by_role = {
            str(item.get("logical_role") or "").casefold(): item
            for item in items
            if isinstance(item, dict)
        }
        files_by_role = {
            str(item.get("logical_role") or "").casefold(): item
            for item in files
            if isinstance(item, dict)
        }
        exact_roles = bool(
            expected_count > 0
            and len(expected_by_role) == expected_count
            and len(items_by_role) == expected_count
            and len(files_by_role) == expected_count
            and set(expected_by_role) == set(items_by_role) == set(files_by_role)
        )
        parent_id = str(receipt.get("drive_folder_id") or "")
        exact_items = True
        correct_parent = bool(parent_id)
        correct_names = True
        sizes_verified = True
        checksums_verified = True
        unique_remote_ids: set[str] = set()
        if exact_roles:
            for role, expected in expected_by_role.items():
                item = items_by_role[role]
                proof = files_by_role[role]
                source = Path(expected["source_path"])
                actual_md5 = _md5_file(source)
                archive_path = str(item.get("archive_path") or "")
                expected_archive_path = expected["archive_path"]
                exact_items = exact_items and archive_path == expected_archive_path
                exact_items = exact_items and item.get("source_path") == str(source)
                exact_items = exact_items and item.get("sha256") == expected["sha256"]
                exact_items = (
                    exact_items and item.get("size_bytes") == expected["size_bytes"]
                )
                exact_items = exact_items and expected["md5"] == actual_md5
                exact_items = exact_items and item.get("md5") == expected["md5"]
                name = Path(archive_path).name
                correct_names = correct_names and bool(name)
                correct_names = correct_names and expected["name"] == name
                correct_names = correct_names and item.get("name") == name
                correct_names = correct_names and proof.get("name") == name
                correct_names = (
                    correct_names and proof.get("archive_path") == archive_path
                )
                remote_id = str(proof.get("drive_file_id") or "")
                correct_parent = (
                    correct_parent and proof.get("drive_folder_id") == parent_id
                )
                sizes_verified = (
                    sizes_verified
                    and proof.get("local_size_bytes") == expected["size_bytes"]
                )
                sizes_verified = (
                    sizes_verified
                    and proof.get("remote_size_bytes") == expected["size_bytes"]
                )
                local_sha = proof.get("local_sha256")
                remote_sha = proof.get("remote_sha256")
                local_md5 = proof.get("local_md5")
                remote_md5 = proof.get("remote_md5")
                strong_checksum = bool(
                    local_sha == expected["sha256"]
                    and (
                        remote_sha == expected["sha256"]
                        or (local_md5 == actual_md5 and remote_md5 == actual_md5)
                    )
                )
                checksums_verified = checksums_verified and strong_checksum
                exact_items = exact_items and proof.get("verified") is True
                exact_items = exact_items and bool(remote_id)
                unique_remote_ids.add(remote_id)
        else:
            exact_items = correct_parent = correct_names = False
            sizes_verified = checksums_verified = False

        exact_counts = bool(
            receipt.get("expected_item_count") == expected_count
            and receipt.get("exact_item_count") == expected_count
            and receipt.get("verified_item_count") == expected_count
            and receipt.get("actual_item_count") == expected_count
            and receipt.get("remote_item_count") == expected_count
        )
        duplicate_absence = bool(
            receipt.get("duplicate_count") == 0
            and len(unique_remote_ids) == expected_count
        )
        receipt_hash_valid = self._drive_receipt_hash_valid(receipt)
        manifest_exact = bool(
            final_manifest["files"] == expected_files
            and final_manifest["item_count"] == expected_count
            and final_manifest["item_set_hash"]
            == content_hash({"files": expected_files})
        )
        proof = {
            "exact_item_set": bool(
                exact_roles
                and exact_items
                and receipt.get("remote_exact_set_verified") is True
            ),
            "exact_item_count": exact_counts,
            "correct_parent": correct_parent,
            "correct_names": correct_names,
            "size_verified": sizes_verified,
            "checksum_readback_verified": checksums_verified,
            "duplicate_absence": duplicate_absence,
            "receipt_hash_valid": receipt_hash_valid,
            "final_request_manifest_exact": manifest_exact,
            "archive_identity_exact": receipt.get("archive_identity")
            == state["archive_identity"],
            "run_identity_exact": receipt.get("run_id") == state["run_id"],
            "provider_archive_state_verified": bool(
                receipt.get("archive_state") == "VERIFIED"
                and receipt.get("ARCHIVE_VERIFIED") is True
                and not receipt.get("mismatch_reason_codes")
            ),
        }
        if not all(proof.values()):
            failed = ",".join(sorted(key for key, value in proof.items() if not value))
            raise RuntimeError(f"MR1_DRIVE_ARCHIVE_PROOF_INCOMPLETE:{failed}")
        return proof

    @staticmethod
    def _drive_receipt_hash_valid(receipt: dict[str, Any]) -> bool:
        keys = {
            "schema_version",
            "run_id",
            "archive_identity",
            "archive_manifest_hash",
            "root_relative_path",
            "drive_folder_id",
            "expected_item_count",
            "verified_item_count",
            "remote_item_count",
            "total_local_size_bytes",
            "total_remote_size_bytes",
            "items",
            "files",
            "remote_exact_set_verified",
            "archive_state",
            "mismatch_reason_codes",
            "provider_call_made",
            "transport",
            "verified_at",
        }
        supplied = receipt.get("receipt_hash")
        base = {key: receipt.get(key) for key in keys}
        return isinstance(supplied, str) and supplied == content_hash(base)

    @staticmethod
    def _drive_verification_exact(verification: Any) -> bool:
        return bool(
            isinstance(verification, dict)
            and set(verification) == DRIVE_VERIFICATION_KEYS
            and all(verification[key] is True for key in DRIVE_VERIFICATION_KEYS)
        )

    def _resolve_exact_authority(self, command: MR1StartCommand) -> dict[str, Any]:
        reapproval = MR1ReapprovalService(self.session).read_approval(
            command.project_id
        )
        if (
            reapproval["MR1_REAPPROVAL_FINAL"] != "PASS"
            or reapproval["PROCEED_TO_MR1"] is not True
            or reapproval["approval_id"] != str(command.approval_id)
            or reapproval["approval_content_hash"] != command.approval_content_hash
            or reapproval["exact_target"]["project_id"] != str(command.project_id)
            or reapproval["exact_target"]["package_artifact_version_id"]
            != str(command.package_artifact_version_id)
        ):
            raise ValidationFailureError(
                "MR1_APPROVAL_HASH_OR_PACKAGE_BINDING_MISMATCH"
            )
        package = self._exact_version(
            command.package_artifact_version_id,
            reapproval["exact_target"]["package_content_hash"],
            "package_manifest",
            command.project_id,
            status_policy="approved_package",
        )
        project = self.session.get(VideoProject, command.project_id)
        if project is None:
            raise ValidationFailureError("MR1_EXACT_PROJECT_MISSING")
        exact_bindings = reapproval["exact_bindings"]
        profile_id = exact_bindings["channel_profile_version"]["id"]
        snapshot_id = exact_bindings["compiled_channel_policy_snapshot"]["id"]
        if (
            self.expected_profile_id is not None
            and profile_id != self.expected_profile_id
        ) or (
            self.expected_snapshot_id is not None
            and snapshot_id != self.expected_snapshot_id
        ):
            raise ValidationFailureError("MR1_PROFILE_SNAPSHOT_BINDING_MISMATCH")
        package_content = deepcopy(package.content or {})
        artifact_authority = MR1ReapprovalService(
            self.session
        ).resolve_package_artifact_authority(
            project=project,
            package=package,
        )
        resolved: dict[str, Any] = {
            key: {
                "artifact_id": str(version.artifact_id),
                "artifact_version_id": str(version.id),
                "artifact_version_ref": f"artifact-version://{version.id}",
                "version_number": version.version_number,
                "content_hash": version.content_hash,
                "content": deepcopy(version.content or {}),
            }
            for key, version in artifact_authority["versions"].items()
            if key in MR1_REQUIRED_EFFECTIVE_ARTIFACT_TYPES
        }
        supplemental = artifact_authority.get("supplemental_visual_alignment")
        if supplemental is not None:
            resolved["supplemental_visual_alignment"] = {
                "artifact_id": str(supplemental.artifact_id),
                "artifact_version_id": str(supplemental.id),
                "artifact_version_ref": f"artifact-version://{supplemental.id}",
                "version_number": supplemental.version_number,
                "content_hash": supplemental.content_hash,
                "content": deepcopy(supplemental.content or {}),
            }
        frozen_policy = self._revalidate_profile_market_rights_authority(
            command=command,
            exact_bindings=exact_bindings,
            resolved=resolved,
            package_variant=artifact_authority["variant"],
        )
        authority = {
            "approval_id": str(command.approval_id),
            "approval_content_hash": command.approval_content_hash,
            "approval_ref": reapproval["approval_ref"],
            "approval_receipt_artifact_version_id": reapproval[
                "approval_receipt_artifact_version_id"
            ],
            "project_id": str(command.project_id),
            "exact_target": deepcopy(reapproval["exact_target"]),
            "package_artifact_version_id": str(package.id),
            "package_content_hash": package.content_hash,
            "package": package_content,
            "package_variant": artifact_authority["variant"],
            "authority_project_ids": deepcopy(
                artifact_authority.get("authority_project_ids")
            ),
            "exact_bindings": deepcopy(exact_bindings),
            "provider_attempt_scope": deepcopy(reapproval["provider_attempt_scope"]),
            "cost_scope": deepcopy(reapproval["cost_scope"]),
            "destination": deepcopy(reapproval["destination"]),
            "frozen_channel_policy": frozen_policy,
            "lpro1_execution_contract": deepcopy(
                reapproval["lpro1_execution_contract"]
            ),
            "reuse_decision_manifest": deepcopy(reapproval.get("reuse_decision")),
            "reuse_decision_manifest_ref": (
                {
                    "artifact_version_id": reapproval[
                        "reuse_decision_artifact_version_id"
                    ],
                    "content_hash": reapproval["reuse_decision_content_hash"],
                }
                if reapproval.get("reuse_decision_artifact_version_id")
                else None
            ),
            "resolved": resolved,
        }
        authority["candidate_authority_bindings"] = (
            self._freeze_candidate_authority_bindings(authority)
        )
        return authority

    @staticmethod
    def _visual_route_authority(
        authority: dict[str, Any],
    ) -> MR1VisualRouteAuthority:
        try:
            return resolve_mr1_visual_route_authority(authority)
        except ValueError as exc:
            raise ValidationFailureError(str(exc)) from exc

    @staticmethod
    def _state_pexels_scenes(state: dict[str, Any]) -> tuple[str, ...]:
        """Read new route state, with an immutable-ledger fallback for old runs."""

        routes = state.get("approved_visual_routes")
        if routes is not None:
            if (
                not isinstance(routes, dict)
                or set(routes) != set(ALL_SCENES)
                or not all(isinstance(value, str) for value in routes.values())
            ):
                raise ValidationFailureError(
                    "MR1_PERSISTED_VISUAL_ROUTE_AUTHORITY_INVALID"
                )
            return tuple(
                scene_id
                for scene_id in ALL_SCENES
                if routes[scene_id] == "PEXELS_VIDEO"
            )

        # Historical runs predate approved_visual_routes.  Their durable attempt
        # ledgers are the exact record of which scene flows were authorized.
        attempt_scenes = {
            str(item.get("scene_id"))
            for item in (state.get("attempts") or {}).values()
            if isinstance(item, dict)
            and item.get("provider") == "pexels_api"
            and item.get("scene_id") in ALL_SCENES
        }
        return tuple(scene_id for scene_id in ALL_SCENES if scene_id in attempt_scenes)

    def _revalidate_profile_market_rights_authority(
        self,
        *,
        command: MR1StartCommand,
        exact_bindings: dict[str, Any],
        resolved: dict[str, Any],
        package_variant: str,
    ) -> dict[str, Any]:
        """Reopen actual DB bytes for every non-package MR1 authority.

        An approval receipt is only a pointer.  Provider execution must prove
        that the exact profile, compiled snapshot, market, niche, destination,
        rights, disclosure and provenance contents still hash and still express
        the approved semantics.  The resulting payload is archived with the run.
        """

        visual_routes = self._visual_route_authority({"resolved": resolved})

        profile_binding = exact_bindings.get("channel_profile_version") or {}
        snapshot_binding = exact_bindings.get("compiled_channel_policy_snapshot") or {}
        try:
            profile = self.session.get(
                ChannelProfileVersion, uuid.UUID(str(profile_binding["id"]))
            )
            snapshot = self.session.get(
                CompiledChannelPolicySnapshot,
                uuid.UUID(str(snapshot_binding["id"])),
            )
        except (KeyError, ValueError) as exc:
            raise ValidationFailureError(
                "MR1_PROFILE_SNAPSHOT_BINDING_INVALID"
            ) from exc
        project = self.session.get(VideoProject, command.project_id)
        checks: dict[str, bool] = {
            "profile_exists": profile is not None,
            "snapshot_exists": snapshot is not None,
            "project_exists": project is not None,
        }
        if profile is None or snapshot is None or project is None:
            raise ValidationFailureError(
                "MR1_PROFILE_SNAPSHOT_DATABASE_AUTHORITY_MISSING"
            )
        checks.update(
            {
                "profile_v3_exact": bool(
                    profile.version == 3
                    and str(profile.id) == str(profile_binding.get("id"))
                    and profile.profile_input_hash
                    == profile_binding.get("content_hash")
                    and content_hash(profile.profile_input)
                    == profile.profile_input_hash
                ),
                "snapshot_exact": bool(
                    str(snapshot.id) == str(snapshot_binding.get("id"))
                    and snapshot.snapshot_version
                    == int(snapshot_binding.get("version") or 0)
                    and snapshot.content_hash == snapshot_binding.get("content_hash")
                    and content_hash(snapshot.compiled_payload) == snapshot.content_hash
                    and snapshot.profile_input_hash == profile.profile_input_hash
                    and snapshot.channel_profile_version_id == profile.id
                ),
                "project_profile_snapshot_exact": bool(
                    project.channel_profile_version_id == profile.id
                    and project.policy_snapshot_id == snapshot.id
                ),
            }
        )
        try:
            policy = ChannelScopedPolicy.model_validate(
                (snapshot.compiled_payload or {}).get("channel_scoped_policy")
            )
        except Exception as exc:
            raise ValidationFailureError("MR1_FROZEN_CHANNEL_POLICY_INVALID") from exc
        market = policy.target_market_profile
        market_digest = policy.target_market_digest
        destination_policy = policy.destination_binding_policy
        if market is None or market_digest is None or destination_policy is None:
            raise ValidationFailureError("MR1_FROZEN_MARKET_AUTHORITY_MISSING")
        destination = destination_policy.destination

        market_binding = exact_bindings.get("target_market_profile") or {}
        digest_binding = exact_bindings.get("target_market_digest") or {}
        destination_binding = exact_bindings.get("destination_binding") or {}
        market_wrapper = resolved["target_market_profile"]["content"]
        digest_wrapper = resolved["target_market_digest"]["content"]
        destination_wrapper = resolved["destination_binding"]["content"]
        checks.update(
            {
                "target_market_exact": bool(
                    market.content_hash == market_binding.get("content_hash")
                    and market_wrapper.get("canonical_hash") == market.content_hash
                    and market.primary_market == "US"
                    and market.target_market == "US"
                    and market.primary_locale == "en-US"
                    and market.narration_locale == "en-US"
                    and market.primary_timezone == "America/New_York"
                    and market.currency == "USD"
                ),
                "market_digest_exact": bool(
                    market_digest.content_hash == digest_binding.get("content_hash")
                    and digest_wrapper.get("canonical_hash")
                    == market_digest.content_hash
                    and market_digest.profile_hash == market.content_hash
                    and market_digest.primary_market == "US"
                    and market_digest.primary_locale == "en-US"
                    and market_digest.narration_locale == "en-US"
                    and market_digest.currency == "USD"
                ),
                "destination_exact_and_publish_blocked": bool(
                    destination.content_hash == destination_binding.get("content_hash")
                    and destination_wrapper.get("canonical_hash")
                    == destination.content_hash
                    and destination.platform == "YOUTUBE"
                    and destination.channel_handle == "@SmallTeamAI"
                    and destination.target_market == "US"
                    and destination.primary_market == "US"
                    and destination.primary_locale == "en-US"
                    and destination.destination_status == "PENDING_PLATFORM_ID"
                    and destination.platform_channel_id is None
                    and destination.credential_ref is None
                    and destination.verification_state != "VERIFIED"
                    and destination.manual_publish_required is True
                ),
            }
        )

        niche = resolved["niche_alignment_dossier"]["content"]
        dossier = resolved["market_alignment_dossier"]["content"]
        required_market_gates = {
            "research_jurisdiction_gate",
            "script_market_alignment_gate",
            "voice_locale_alignment_gate",
            "visual_market_alignment_gate",
            "thumbnail_market_alignment_gate",
            "metadata_market_alignment_gate",
        }
        sc04_variant = package_variant == SC04_PROJECT_TYPE
        if sc04_variant:
            # Old dossier visual components were reviewed against the superseded
            # Pexels SC-04 plan.  They remain usable only for these nonvisual
            # gates; the exact supplemental artifact below is the sole current
            # visual market/niche authority.
            required_market_gates.remove("visual_market_alignment_gate")
        passing_market_gates = {
            item.get("gate_key")
            for item in dossier.get("component_results") or []
            if item.get("verdict") == "PASS"
        }
        rights = resolved["rights_disclosure_completeness_report"]["content"]
        disclosure = resolved["synthetic_media_disclosure_receipt_draft"]["content"]
        provenance = resolved["asset_provenance_plan"]["content"]
        visual = resolved["visual_direction_contract"]["content"]
        risk = resolved["publish_risk_dossier"]["content"]
        risk_destination = risk.get("destination_binding") or {}
        supplemental_visual = (resolved.get("supplemental_visual_alignment") or {}).get(
            "content"
        ) or {}
        checks.update(
            {
                "niche_alignment_exact_pass": bool(
                    (not sc04_variant and niche.get("overall_verdict") == "PASS")
                    or (
                        sc04_variant
                        and (supplemental_visual.get("niche_alignment") or {}).get(
                            "verdict"
                        )
                        == "PASS"
                    )
                ),
                "market_alignment_exact_pass": bool(
                    (sc04_variant or dossier.get("overall_verdict") == "PASS")
                    and required_market_gates.issubset(passing_market_gates)
                    and (
                        not sc04_variant
                        or (supplemental_visual.get("market_alignment") or {}).get(
                            "verdict"
                        )
                        == "PASS"
                    )
                ),
                "current_visual_authority_exact": bool(
                    not sc04_variant
                    or (
                        supplemental_visual.get("subject")
                        == {
                            "artifact_id": resolved["visual_plan"]["artifact_id"],
                            "artifact_version_id": resolved["visual_plan"][
                                "artifact_version_id"
                            ],
                            "artifact_version_ref": resolved["visual_plan"][
                                "artifact_version_ref"
                            ],
                            "version_number": resolved["visual_plan"]["version_number"],
                            "content_hash": resolved["visual_plan"]["content_hash"],
                        }
                        and supplemental_visual.get("all_required_checks_pass") is True
                    )
                ),
                "rights_planning_exact_pass": bool(
                    rights.get("planning_state") == "PASS"
                    and rights.get("decision") == "PASS"
                    and rights.get("provider_outputs_claimed") is False
                    and rights.get("generated_evidence_authority") is False
                    and rights.get("archive_before_purge") is True
                ),
                "synthetic_disclosure_exact": bool(
                    disclosure.get("receipt_status") == "PRE_RENDER_PLANNED"
                    and disclosure.get("provider_outputs_exist") is False
                    and disclosure.get("synthetic_voice_planned") is True
                    and disclosure.get("synthetic_image_planned") is False
                    and disclosure.get("synthetic_video_planned") is False
                ),
                "asset_provenance_exact": bool(
                    provenance.get("provider_output_exists") is False
                    and provenance.get("generated_evidence_authority") is False
                    and set(
                        (provenance.get("native_assets") or {}).get("scene_ids") or []
                    )
                    == set(visual_routes.native_scenes)
                    and set(
                        (provenance.get("pexels") or {}).get("planned_scenes") or []
                    )
                    == set(visual_routes.pexels_scenes)
                ),
                "visual_market_profile_exact": visual.get("niche_visual_source_profile")
                == "STOCK_ASSISTED",
                "publish_risk_boundary_exact": bool(
                    (risk.get("rights_provenance_risk") or {}).get("decision")
                    == "PASS_PLANNING"
                    and risk_destination.get("status") == "PENDING_PLATFORM_ID"
                    and risk_destination.get("publish_execution_allowed") is False
                    and risk_destination.get("publish_blocker") == "PENDING_PLATFORM_ID"
                    and (risk.get("manual_publish_boundary") or {}).get("required")
                    is True
                    and (risk.get("manual_publish_boundary") or {}).get(
                        "automatic_publish"
                    )
                    is False
                ),
            }
        )
        failed = sorted(key for key, value in checks.items() if value is not True)
        if failed:
            raise ValidationFailureError(
                "MR1_PROFILE_MARKET_RIGHTS_REVALIDATION_FAILED:" + ",".join(failed)
            )
        return {
            "schema_version": "mr1.frozen-channel-authority.v1",
            "profile_id": str(profile.id),
            "profile_hash": profile.profile_input_hash,
            "profile_input": deepcopy(profile.profile_input),
            "snapshot_id": str(snapshot.id),
            "snapshot_hash": snapshot.content_hash,
            "compiled_payload": deepcopy(snapshot.compiled_payload),
            "channel_scoped_policy": policy.model_dump(mode="json"),
            "checks": {key: "PASS" for key in sorted(checks)},
            "result": "PASS",
        }

    def _master_preflight(
        self,
        *,
        command: MR1StartCommand,
        authority: dict[str, Any],
        gateway_readiness: dict[str, Any],
    ) -> dict[str, Any]:
        plan = authority["resolved"]["provider_execution_plan"]["content"]
        visual_routes = self._visual_route_authority(authority)
        routes = {
            item["scene_id"]: item["route"] for item in plan.get("scene_routes") or []
        }
        stages = {item["provider"]: item for item in plan.get("stages") or []}
        reuse_manifest = authority.get("reuse_decision_manifest") or {}
        reuse_allowed = set(reuse_manifest.get("reuse_allowed_output_keys") or [])
        expected_drive_phases = DRIVE_IDEMPOTENCY_PHASES
        sc04_variant = authority.get("package_variant") == SC04_PROJECT_TYPE
        drive_planned_requests = int(stages["google_drive"]["planned_requests"])
        drive_stage_phases = stages["google_drive"].get("idempotency_phases") or []
        drive_attempt_phases = authority["provider_attempt_scope"].get(
            "drive_idempotency_phases"
        ) or []
        drive_phase_count = authority["provider_attempt_scope"].get(
            "drive_phase_count"
        )
        drive_phases_distinct = authority["provider_attempt_scope"].get(
            "drive_phases_are_distinct_authorized_mutations"
        )
        drive_authority_exact = bool(
            stages["google_drive"].get("operation")
            == "canonical_review_archive_plus_finalization_supplement"
            and drive_planned_requests == 2
            and drive_stage_phases == expected_drive_phases
            and drive_attempt_phases == expected_drive_phases
            and drive_phase_count == 2
            and drive_phases_distinct is True
        )
        fresh_provider_call_plan = {
            "elevenlabs_narration": (0 if "narration_audio" in reuse_allowed else 1),
            "elevenlabs_forced_alignment": (
                0 if "forced_alignment" in reuse_allowed else 1
            ),
            "pexels_api": len(visual_routes.pexels_scenes),
            "google_drive": drive_planned_requests,
        }
        cost = authority["resolved"]["cost_estimate_snapshot"]["content"]
        pacing = authority["resolved"]["narration_pacing_preflight_estimate"]["content"]
        budget_preflights = {
            provider: self._monthly_budget_evidence(
                authority=authority,
                run_id=command.approval_id,
                provider=provider,
                operation_key=f"master-preflight:{provider}",
            )
            for provider in (
                "elevenlabs",
                "forced_alignment",
                "pexels_api",
                "google_drive",
            )
        }
        repository_root = Path(__file__).resolve().parents[2]
        exact_gateway_set = {
            "elevenlabs_narration",
            "forced_alignment",
            "pexels_api",
            "google_drive",
        }
        checks = {
            "repository_identity": bool(
                (repository_root / ".git").exists()
                and (
                    repository_root / "config" / "artifact_type_registry.yaml"
                ).is_file()
                and (
                    repository_root / "scripts" / "run_mr1_real_production.py"
                ).is_file()
            ),
            "exact_package_hash": (
                authority["package_artifact_version_id"]
                == str(command.package_artifact_version_id)
                and authority["package_content_hash"]
                == authority["exact_target"].get("package_content_hash")
            ),
        }
        checks.update(
            {
                "profile_snapshot_lineage": bool(
                    (authority.get("frozen_channel_policy") or {}).get("result")
                    == "PASS"
                    and (authority.get("frozen_channel_policy") or {}).get("profile_id")
                    == authority["exact_bindings"]["channel_profile_version"]["id"]
                    and (authority.get("frozen_channel_policy") or {}).get(
                        "snapshot_id"
                    )
                    == authority["exact_bindings"]["compiled_channel_policy_snapshot"][
                        "id"
                    ]
                ),
                "destination_render_eligible": authority["destination"].get(
                    "destination_status"
                )
                == "PENDING_PLATFORM_ID",
                "publish_stays_blocked": authority["destination"].get(
                    "publish_execution_ready"
                )
                is not True,
                "approved_runtime_hard_limits_exact": bool(
                    (pacing.get("target_runtime_minutes") or {}).get("minimum") == 6.0
                    and (pacing.get("target_runtime_minutes") or {}).get("maximum")
                    == 12.0
                    and pacing.get("advisory_only") is True
                    and pacing.get("canonical_timing_authority") is False
                ),
                "provider_plan_exact": (
                    routes == dict(visual_routes.routes)
                    # The package stage counts declare provider capabilities.
                    # The exact fresh-run call plan below subtracts immutable
                    # outputs expressly authorized by the reuse manifest.
                    and int(stages["elevenlabs"]["planned_requests"]) == 1
                    and int(stages["forced_alignment"]["planned_requests"]) == 1
                    and int(stages["pexels_api"]["planned_requests"])
                    == len(visual_routes.pexels_scenes)
                    and int(stages["native_graphics"]["planned_requests"])
                    == len(visual_routes.native_scenes)
                    and int(stages["google_gemini_image"]["planned_requests"]) == 0
                    and int(stages["google_veo"]["planned_requests"]) == 0
                    and drive_authority_exact
                    and (
                        not sc04_variant
                        or (
                            reuse_manifest.get("fresh_provider_call_plan", {}).get(
                                "elevenlabs_narration"
                            )
                            == fresh_provider_call_plan["elevenlabs_narration"]
                            and reuse_manifest.get(
                                "fresh_provider_call_plan", {}
                            ).get("elevenlabs_forced_alignment")
                            == fresh_provider_call_plan[
                                "elevenlabs_forced_alignment"
                            ]
                        )
                    )
                ),
                "no_provider_fallback": plan.get("automatic_pexels_to_ai_fallback")
                is False
                and plan.get("external_ai_video_fallback") is False,
                "attempt_limits": all(
                    int(item.get("attempt_cap") or 0)
                    == (
                        1
                        if visual_routes.routes[item["scene_id"]] == "PEXELS_VIDEO"
                        else 0
                    )
                    for item in plan.get("scene_routes") or []
                ),
                "cost_hard_cap": float(
                    cost.get("hard_cap_usd")
                    or cost.get("hard_cap")
                    or authority["cost_scope"].get("hard_cap_usd")
                    or 0
                )
                == 1.0
                and cost.get("estimated_cost") is not None
                and float(cost["estimated_cost"]) == 0.0
                and cost.get("currency") == "USD",
                "monthly_budget_ledger": all(
                    item.get("result") == "PASS" for item in budget_preflights.values()
                ),
                "rights_disclosure_bound": all(
                    key in authority["exact_bindings"]
                    and key in authority["resolved"]
                    and len(authority["resolved"][key]["content_hash"]) == 64
                    for key in (
                        "rights_disclosure_completeness_report",
                        "synthetic_media_disclosure_receipt_draft",
                        "asset_provenance_plan",
                        "publish_risk_dossier",
                    )
                ),
                "prior_output_reuse_fail_closed": bool(
                    authority.get("package_variant") != SC04_PROJECT_TYPE
                    or (
                        reuse_manifest.get("fail_closed") is True
                        and reuse_manifest.get("fresh_run_required") is True
                        and list(reuse_manifest.get("reuse_allowed_output_keys") or [])
                        in (
                            [],
                            ["narration_audio"],
                            ["narration_audio", "forced_alignment"],
                        )
                        and reuse_manifest.get("prior_output_reuse_count")
                        == len(reuse_allowed)
                        and reuse_manifest.get("canonical_timeline_reuse_authorized")
                        is False
                        and reuse_manifest.get(
                            "supporting_visual_subwindows_reuse_authorized"
                        )
                        is False
                        and reuse_manifest.get("fresh_temporal_compilation_required")
                        is True
                        and reuse_manifest.get("fresh_caption_compilation_required")
                        is True
                        and reuse_manifest.get("fresh_elevenlabs_execution_cost_usd")
                        == (
                            0.0
                            if fresh_provider_call_plan["elevenlabs_narration"] == 0
                            and fresh_provider_call_plan["elevenlabs_forced_alignment"]
                            == 0
                            else None
                        )
                        and reuse_manifest.get("old_provider_cost_reused_or_resettled")
                        is False
                        and content_hash(reuse_manifest)
                        == (authority.get("reuse_decision_manifest_ref") or {}).get(
                            "content_hash"
                        )
                        and bool(
                            (authority.get("reuse_decision_manifest_ref") or {}).get(
                                "artifact_version_id"
                            )
                        )
                        and (reuse_manifest.get("target_package") or {}).get(
                            "artifact_version_id"
                        )
                        == authority["package_artifact_version_id"]
                        and (reuse_manifest.get("target_package") or {}).get(
                            "content_hash"
                        )
                        == authority["package_content_hash"]
                    )
                ),
                "media_workspace_containment": bool(
                    not self.workspace_root.is_symlink()
                    and (
                        (
                            self.workspace_root.is_dir()
                            and os.access(self.workspace_root, os.W_OK | os.X_OK)
                        )
                        or (
                            not self.workspace_root.exists()
                            and self.workspace_root.parent.is_dir()
                            and os.access(self.workspace_root.parent, os.W_OK | os.X_OK)
                        )
                    )
                ),
                "gateway_readiness": set(gateway_readiness) == exact_gateway_set
                and all(
                    item.get("result") == "PASS"
                    and item.get("billable_generation_probe") is False
                    for item in gateway_readiness.values()
                ),
                "drive_archive_required": drive_authority_exact,
                "youtube_prohibited": bool(
                    set(gateway_readiness) == exact_gateway_set
                    and "youtube" not in gateway_readiness
                    and "google_gemini_image" not in gateway_readiness
                    and "google_veo" not in gateway_readiness
                ),
            }
        )
        failed = [key for key, passed in checks.items() if passed is not True]
        if failed:
            raise ValidationFailureError(
                "MR1_MASTER_PREFLIGHT_FAILED:" + ",".join(sorted(failed))
            )
        return {
            "schema_version": "mr1.master-preflight.v1",
            "mode": "READ_ONLY_NO_BILLABLE_PROBE",
            "checks": {key: "PASS" for key in checks},
            "gateway_readiness": deepcopy(gateway_readiness),
            "monthly_budget_evidence": deepcopy(budget_preflights),
            "fresh_provider_call_plan": fresh_provider_call_plan,
            "reused_output_count": len(reuse_allowed),
            "fresh_elevenlabs_execution_cost_usd": reuse_manifest.get(
                "fresh_elevenlabs_execution_cost_usd"
            ),
            "provider_calls": 0,
            "drive_calls": 0,
            "youtube_calls": 0,
            "result": "PASS",
            "exact_target": deepcopy(authority["exact_target"]),
            "created_at": datetime.now(UTC).isoformat(),
        }

    def _gateway_readiness_preflight(
        self, gateways: MR1ProviderGateways
    ) -> dict[str, Any]:
        """Run local/read-only adapter checks before a durable run can exist."""

        results: dict[str, Any] = {}
        for name, gateway in (
            ("elevenlabs_narration", gateways.narration),
            ("forced_alignment", gateways.alignment),
            ("pexels_api", gateways.pexels),
            ("google_drive", gateways.drive),
        ):
            preflight = getattr(gateway, "preflight", None)
            if not callable(preflight):
                raise ValidationFailureError(f"MR1_GATEWAY_PREFLIGHT_REQUIRED:{name}")
            try:
                result = _redact_volatile(_jsonable(preflight()))
            except Exception as exc:
                raise ValidationFailureError(
                    f"MR1_GATEWAY_PREFLIGHT_FAILED:{name}:{type(exc).__name__}:{exc}"
                ) from exc
            if (
                not isinstance(result, dict)
                or result.get("result") != "PASS"
                or result.get("billable_generation_probe") is not False
            ):
                raise ValidationFailureError(f"MR1_GATEWAY_PREFLIGHT_FAILED:{name}")
            results[name] = result
        return results

    def _narration_request(
        self, state: dict[str, Any], authority: dict[str, Any], workspace: Path
    ) -> dict[str, Any]:
        spoken = authority["resolved"]["spoken_text_normalized"]
        script = authority["resolved"]["script"]
        voice = authority["resolved"]["voice_policy"]
        voice_content = voice["content"]
        core = {
            "provider": "elevenlabs",
            "operation": "narration",
            "script_artifact_version_id": script["artifact_version_id"],
            "script_hash": script["content_hash"],
            "spoken_text_artifact_version_id": spoken["artifact_version_id"],
            "spoken_text_hash": spoken["content_hash"],
            "normalized_text_hash": spoken["content"]["normalized_text_hash"],
            "normalized_text": spoken["content"]["normalized_text"],
            "voice_policy_artifact_version_id": voice["artifact_version_id"],
            "voice_policy_content_hash": voice["content_hash"],
            "voice_id": voice_content["voice_identity"]["voice_id"],
            "model_id": voice_content["voice_identity"]["model_id"],
            "voice_settings": deepcopy(voice_content["pacing_policy"]["settings"]),
            "language": "en",
            "narration_locale": "en-US",
            "approval_id": state["approval_id"],
            "approval_content_hash": state["approval_content_hash"],
            "approval_ref": authority["approval_ref"],
            "cost_snapshot_ref": authority["resolved"]["cost_estimate_snapshot"][
                "artifact_version_id"
            ],
            "idempotency_key": f"mr1:{state['run_id']}:elevenlabs:narration",
            "idempotency_fingerprint": _idempotency_fingerprint(
                approval_content_hash=state["approval_content_hash"],
                run_id=state["run_id"],
                provider="elevenlabs",
                operation="narration",
                scene_id=None,
            ),
            "destination": str(workspace / "narration" / "narration.mp3"),
            "attempt_cap": 1,
            "sdk_retry": False,
        }
        return {**core, "request_hash": content_hash(core)}

    def _alignment_request(
        self, state: dict[str, Any], authority: dict[str, Any], workspace: Path
    ) -> dict[str, Any]:
        narration = state["provider_outputs"]["narration"]
        spoken = authority["resolved"]["spoken_text_normalized"]
        core = {
            "provider": "forced_alignment",
            "operation": "forced_alignment",
            "audio_ref": narration.get("audio_asset_ref")
            or narration.get("output_path")
            or narration.get("path"),
            "audio_sha256": narration.get("audio_sha256") or narration.get("sha256"),
            "spoken_text_artifact_version_id": spoken["artifact_version_id"],
            "spoken_text_hash": spoken["content_hash"],
            "normalized_text_hash": spoken["content"]["normalized_text_hash"],
            "normalized_text": spoken["content"]["normalized_text"],
            "spoken_tokens": [
                {
                    **deepcopy(token),
                    "token_id": token.get("token_id")
                    or f"token-{int(token['index']):06d}",
                }
                for token in spoken["content"]["spoken_tokens"]
            ],
            "strict_token_coverage": 1.0,
            "estimated_timing_fallback_allowed": False,
            "approval_id": state["approval_id"],
            "approval_content_hash": state["approval_content_hash"],
            "idempotency_key": f"mr1:{state['run_id']}:elevenlabs:forced_alignment",
            "idempotency_fingerprint": _idempotency_fingerprint(
                approval_content_hash=state["approval_content_hash"],
                run_id=state["run_id"],
                provider="forced_alignment",
                operation="forced_alignment",
                scene_id=None,
            ),
            "destination": str(workspace / "alignment" / "alignment.json"),
            "attempt_cap": 1,
            "sdk_retry": False,
        }
        return {**core, "request_hash": content_hash(core)}

    def _pexels_request(
        self,
        state: dict[str, Any],
        authority: dict[str, Any],
        scene_id: str,
        workspace: Path,
        *,
        operation_key: str | None = None,
    ) -> dict[str, Any]:
        decisions = authority["resolved"]["visual_source_decision_set"]["content"]
        decision = next(
            item for item in decisions["decisions"] if item["scene_id"] == scene_id
        )
        ledger = state["attempts"][operation_key or f"pexels:{scene_id}"]
        if (
            decision["preferred_source_route"] != "PEXELS_VIDEO"
            or decision["provider"] != "pexels_api"
            or int(decision["maximum_automated_attempts"]) != 1
        ):
            raise ValidationFailureError("MR1_PEXELS_DECISION_BINDING_INVALID")
        temporal = state.get("temporal_authority") or {}
        window = next(
            (
                item
                for item in temporal.get("scene_windows") or []
                if item.get("scene_id") == scene_id
            ),
            None,
        )
        if window is None:
            raise ValidationFailureError("MR1_PEXELS_CANONICAL_WINDOW_REQUIRED")
        supporting = next(
            (
                item
                for item in temporal.get("supporting_visual_subwindows") or []
                if item.get("scene_id") == scene_id
            ),
            None,
        )
        if supporting is None:
            raise ValidationFailureError("MR1_PEXELS_SUPPORTING_SUBWINDOW_REQUIRED")
        stock_context = supporting["stock_context"]
        native_explanation = supporting["native_explanation"]
        minimum_duration_seconds = (int(stock_context["duration_ms"]) + 999) // 1000
        if minimum_duration_seconds > 120:
            raise ValidationFailureError(
                "MR1_PEXELS_CANONICAL_SCENE_EXCEEDS_PROVIDER_BOUND"
            )
        pexels_policy = (
            (authority.get("frozen_channel_policy") or {})
            .get("channel_scoped_policy", {})
            .get("provider_usage_policy", {})
            .get("pexels", {})
        )
        semantic_fit_threshold = pexels_policy.get("semantic_fit_threshold")
        if (
            isinstance(semantic_fit_threshold, bool)
            or not isinstance(semantic_fit_threshold, (int, float))
            or not 0 < float(semantic_fit_threshold) <= 1
        ):
            raise ValidationFailureError("MR1_PEXELS_FROZEN_SEMANTIC_THRESHOLD_INVALID")
        excluded_provider_asset_ids = sorted(
            {
                str(
                    output.get("provider_asset_id")
                    or (output.get("selected_candidate") or {}).get("provider_asset_id")
                    or (output.get("selected_candidate") or {}).get("id")
                    or ""
                )
                for key, output in (state.get("provider_outputs") or {}).items()
                if key.startswith("pexels:") and isinstance(output, dict)
            }
            - {""}
        )
        continuation = ledger.get("provider_attempt_continuation")
        query_amendment = ledger.get("provider_query_amendment")
        semantic_intent = decision["semantic_intent"]
        stock_search_intent = semantic_intent
        approved_query_authority: dict[str, Any] | None = None
        if isinstance(continuation, dict):
            stock_search_intent = str(
                continuation.get("approved_stock_search_intent") or ""
            ).strip()
            approved_query_authority = deepcopy(
                continuation.get("approved_query_authority")
            )
            if (
                continuation.get("package_semantic_intent")
                != semantic_intent
                or not stock_search_intent
                or not isinstance(approved_query_authority, dict)
            ):
                raise ValidationFailureError(
                    "MR1_PEXELS_CONTINUATION_QUERY_AUTHORITY_MISSING"
                )
        elif isinstance(query_amendment, dict):
            amendment = (
                query_amendment.get("pending_query_amendments") or {}
            ).get(scene_id)
            if not isinstance(amendment, dict):
                raise ValidationFailureError(
                    "MR1_PEXELS_QUERY_AMENDMENT_AUTHORITY_MISSING"
                )
            stock_search_intent = str(
                amendment.get("approved_stock_search_intent") or ""
            ).strip()
            approved_query_authority = deepcopy(
                amendment.get("approved_query_authority")
            )
            if (
                amendment.get("package_semantic_intent")
                != semantic_intent
                or not stock_search_intent
                or not isinstance(approved_query_authority, dict)
            ):
                raise ValidationFailureError(
                    "MR1_PEXELS_QUERY_AMENDMENT_INVALID"
                )
        idempotency_authority_hash = str(
            ledger.get("idempotency_authority_content_hash")
            or state["approval_content_hash"]
        )
        core = {
            "provider": "pexels_api",
            "operation": "supporting_asset_acquisition",
            "scene_id": scene_id,
            "route": "PEXELS_VIDEO",
            "semantic_intent": semantic_intent,
            "stock_search_intent": stock_search_intent,
            "stock_search_intent_scope": (
                "PEXELS_SUPPORTING_SUBWINDOW_CONTEXT_ONLY"
            ),
            "semantic_fit_threshold": float(semantic_fit_threshold),
            "semantic_fit_threshold_authority": (
                "frozen_channel_policy.provider_usage_policy.pexels."
                "semantic_fit_threshold"
            ),
            "canonical_timeline_hash": temporal["timeline_hash"],
            "timing_authority": "CANONICAL_MEDIA_TIMELINE",
            "scene_start_ms": int(window["start_ms"]),
            "scene_end_ms": int(window["end_ms"]),
            "scene_duration_ms": int(window["duration_ms"]),
            "supporting_visual_subwindows_hash": temporal[
                "supporting_visual_subwindows_hash"
            ],
            "stock_context_start_ms": int(stock_context["start_ms"]),
            "stock_context_end_ms": int(stock_context["end_ms"]),
            "stock_context_duration_ms": int(stock_context["duration_ms"]),
            "native_explanation_start_ms": int(native_explanation["start_ms"]),
            "native_explanation_end_ms": int(native_explanation["end_ms"]),
            "native_explanation_duration_ms": int(native_explanation["duration_ms"]),
            "native_mechanism": supporting["native_mechanism"],
            "supporting_subwindow_policy_ref": supporting["policy_ref"],
            "minimum_duration_seconds": float(minimum_duration_seconds),
            "maximum_duration_seconds": 120.0,
            "estimated_timing_fallback_used": False,
            "target_market": "US",
            "market_context": "US_SMALL_BUSINESS",
            "observable_reality_support_only": True,
            "generated_evidence_authority": False,
            "automatic_pexels_to_ai_fallback": False,
            "provider_substitution_allowed": False,
            "excluded_provider_asset_ids": excluded_provider_asset_ids,
            "approval_id": state["approval_id"],
            "approval_content_hash": state["approval_content_hash"],
            "idempotency_key": ledger["idempotency_key"],
            "idempotency_fingerprint": _idempotency_fingerprint(
                approval_content_hash=idempotency_authority_hash,
                run_id=state["run_id"],
                provider="pexels_api",
                operation="supporting_asset_acquisition",
                scene_id=scene_id,
            ),
            "destination": str(workspace / "source_assets" / f"{scene_id}.mp4"),
            "attempt_cap": 1,
            "sdk_retry": False,
        }
        if isinstance(continuation, dict):
            core["provider_attempt_continuation_approval_id"] = continuation[
                "approval_decision_id"
            ]
            core["provider_attempt_continuation_authorization_hash"] = continuation[
                "authorization_content_hash"
            ]
            core["provider_attempt_ordinal"] = int(
                ledger.get("provider_attempt_ordinal") or 2
            )
            core["approved_query_authority"] = approved_query_authority
        elif isinstance(query_amendment, dict):
            core["provider_query_amendment_approval_id"] = query_amendment[
                "approval_decision_id"
            ]
            core["provider_query_amendment_authorization_hash"] = (
                query_amendment["authorization_content_hash"]
            )
            core["approved_query_authority"] = approved_query_authority
        return {**core, "request_hash": content_hash(core)}

    def _validate_temporal_prepass(
        self,
        state: dict[str, Any],
        temporal: dict[str, Any],
    ) -> None:
        pexels_scenes = self._state_pexels_scenes(state)
        windows = temporal.get("scene_windows")
        by_scene = {
            item.get("scene_id"): item
            for item in windows or []
            if isinstance(item, dict)
        }
        supporting = temporal.get("supporting_visual_subwindows")
        supporting_by_scene = {
            item.get("scene_id"): item
            for item in supporting or []
            if isinstance(item, dict)
        }
        narration = state["provider_outputs"].get("narration") or {}
        required = (
            isinstance(temporal, dict),
            temporal.get("result") == "PASS",
            temporal.get("state") == "CANONICAL_TIMELINE_READY",
            temporal.get("run_id") == state["run_id"],
            temporal.get("timing_authority") == "CANONICAL_MEDIA_TIMELINE",
            temporal.get("estimated_timing_fallback_used") is False,
            temporal.get("automatic_visual_fallback_used") is False,
            temporal.get("provider_calls_made_by_continuation") == 0,
            isinstance(temporal.get("timeline_hash"), str)
            and len(temporal["timeline_hash"]) == 64,
            temporal.get("token_coverage") == 1.0,
            temporal.get("audio_duration_ms")
            == (narration.get("audio_duration_ms") or narration.get("duration_ms")),
            set(by_scene) == set(ALL_SCENES),
            len(windows or []) == len(ALL_SCENES),
            set(supporting_by_scene) == set(pexels_scenes),
            len(supporting or []) == len(pexels_scenes),
            isinstance(temporal.get("supporting_visual_subwindows_hash"), str)
            and len(temporal["supporting_visual_subwindows_hash"]) == 64,
        )
        if not all(required):
            raise ValidationFailureError("MR1_TEMPORAL_PREPASS_INVALID")
        ordered = [by_scene[scene_id] for scene_id in ALL_SCENES]
        prior_end = 0
        for item in ordered:
            start = int(item.get("start_ms") or 0)
            end = int(item.get("end_ms") or 0)
            duration = int(item.get("duration_ms") or 0)
            if start < prior_end or end <= start or duration != end - start:
                raise ValidationFailureError(
                    "MR1_TEMPORAL_PREPASS_SCENE_WINDOWS_INVALID"
                )
            prior_end = end
        if prior_end != int(temporal["audio_duration_ms"]):
            raise ValidationFailureError(
                "MR1_TEMPORAL_PREPASS_DURATION_COVERAGE_INVALID"
            )
        policy_ref = (
            "mr1-temporal-policy://supporting-stock-subwindow/"
            "min-8000ms-or-floor-20pct/v1"
        )
        for scene_id in pexels_scenes:
            scene = by_scene[scene_id]
            item = supporting_by_scene[scene_id]
            stock = item.get("stock_context") or {}
            native = item.get("native_explanation") or {}
            scene_duration = int(scene["duration_ms"])
            expected_stock_duration = min(8_000, (scene_duration * 20) // 100)
            checks = (
                item.get("native_mechanism") == PEXELS_NATIVE_MECHANISMS[scene_id],
                item.get("policy_ref") == policy_ref,
                int(stock.get("start_ms") or -1) == int(scene["start_ms"]),
                int(stock.get("duration_ms") or 0) == expected_stock_duration,
                int(stock.get("end_ms") or -1)
                == int(scene["start_ms"]) + expected_stock_duration,
                int(native.get("start_ms") or -1) == int(stock.get("end_ms")),
                int(native.get("end_ms") or -1) == int(scene["end_ms"]),
                int(native.get("duration_ms") or 0)
                == scene_duration - expected_stock_duration,
                0 < expected_stock_duration < scene_duration,
            )
            if not all(checks):
                raise ValidationFailureError(
                    f"MR1_SUPPORTING_VISUAL_SUBWINDOW_INVALID:{scene_id}"
                )
        subwindow_manifest = {
            "schema_version": "mr1.supporting-visual-subwindows.v1",
            "timeline_hash": temporal["timeline_hash"],
            "policy_ref": policy_ref,
            "supporting_visual_subwindows": supporting,
        }
        if content_hash(subwindow_manifest) != temporal.get(
            "supporting_visual_subwindows_hash"
        ):
            raise ValidationFailureError("MR1_SUPPORTING_VISUAL_SUBWINDOW_HASH_INVALID")

    def _runtime_submit_preflight(
        self,
        state: dict[str, Any],
        operation_key: str,
        request: dict[str, Any],
    ) -> None:
        # Provider submit is the final authority boundary. The session is
        # configured with expire_on_commit=False, so discard every identity-map
        # snapshot before reopening and row-locking exact database authority.
        self.session.flush()
        self.session.expire_all()
        ledger = state["attempts"][operation_key]
        approval = self.session.get(
            ApprovalDecision,
            uuid.UUID(state["approval_id"]),
            populate_existing=True,
            with_for_update=True,
        )
        canonical = MR1ReapprovalService(self.session).read_approval(
            uuid.UUID(state["project_id"])
        )
        authority = self._resolve_exact_authority(
            MR1StartCommand(
                approval_id=uuid.UUID(state["approval_id"]),
                approval_content_hash=state["approval_content_hash"],
                project_id=uuid.UUID(state["project_id"]),
                package_artifact_version_id=uuid.UUID(
                    state["package_artifact_version_id"]
                ),
            )
        )
        attempt_artifact = self.session.get(
            Artifact,
            uuid.UUID(state["attempt_artifact_ids"][operation_key]),
            populate_existing=True,
            with_for_update=True,
        )
        persisted_ledger = (
            self.session.get(
                ArtifactVersion,
                attempt_artifact.current_version_id,
                populate_existing=True,
                with_for_update=True,
            )
            if attempt_artifact is not None
            and attempt_artifact.current_version_id is not None
            else None
        )
        persisted_content = deepcopy(
            (persisted_ledger.content or {}) if persisted_ledger is not None else {}
        )
        expected = content_hash(
            {key: value for key, value in request.items() if key != "request_hash"}
        )
        idempotency_authority_hash = str(
            ledger.get("idempotency_authority_content_hash")
            or state["approval_content_hash"]
        )
        expected_idempotency_fingerprint = _idempotency_fingerprint(
            approval_content_hash=idempotency_authority_hash,
            run_id=state["run_id"],
            provider=ledger["provider"],
            operation=ledger["operation"],
            scene_id=ledger.get("scene_id"),
        )
        budget_binding = ledger.get("monthly_budget_evidence") or {}
        reservation_ref = str(
            (state.get("monthly_budget_reservation") or {}).get("reservation_ref")
            or budget_binding.get("reservation_ref")
            or ""
        )
        fresh_budget_evidence = MR1MonthlyBudgetAuthority(self.session).inspect(
            reservation_ref
        )
        resumable_drive = bool(
            operation_key == "google_drive:archive"
            and ledger["state"] == "RESUMABLE_FAILURE"
            and ledger["attempt_count"] == ledger["attempt_cap"] == 1
        )
        continuation = ledger.get("provider_attempt_continuation")
        continuation_authority_valid = continuation is None
        if isinstance(continuation, dict):
            try:
                continuation_decision = self.session.get(
                    ApprovalDecision,
                    uuid.UUID(str(continuation["approval_decision_id"])),
                    populate_existing=True,
                    with_for_update=True,
                )
                continuation_review_task = self.session.get(
                    ReviewTask,
                    uuid.UUID(str(continuation["operator_review_task_id"])),
                    populate_existing=True,
                    with_for_update=True,
                )
                review_manifest_version = self._exact_version(
                    uuid.UUID(
                        str(
                            continuation[
                                "operator_review_manifest_artifact_version_id"
                            ]
                        )
                    ),
                    str(
                        continuation[
                            "operator_review_manifest_content_hash"
                        ]
                    ),
                    PROVIDER_CONTINUATION_REVIEW_ARTIFACT_TYPE,
                    uuid.UUID(state["project_id"]),
                    fresh_lock=True,
                )
            except (
                KeyError,
                TypeError,
                ValueError,
                ValidationFailureError,
            ):
                continuation_decision = None
                continuation_review_task = None
                review_manifest_version = None
            dynamic_request_fields = {
                "approval_id",
                "approval_content_hash",
                "idempotency_key",
                "idempotency_fingerprint",
                "destination",
                "request_hash",
                "provider_attempt_continuation_approval_id",
                "provider_attempt_continuation_authorization_hash",
                "provider_attempt_ordinal",
                "provider_query_amendment_approval_id",
                "provider_query_amendment_authorization_hash",
            }
            request_invariants = {
                key: deepcopy(value)
                for key, value in request.items()
                if key not in dynamic_request_fields
            }
            receipt_without_hash = {
                key: value
                for key, value in continuation.items()
                if key != "receipt_content_hash"
            }
            authorization_scope = {
                key: value
                for key, value in continuation.items()
                if key
                not in {
                    "approval_decision_id",
                    "authorization_content_hash",
                    "decided_by_user_id",
                    "decided_at",
                    "receipt_content_hash",
                }
            }
            authorization_hash = content_hash(authorization_scope)
            continuation_scene_id = str(continuation.get("scene_id") or "")
            base_operation_key = f"pexels:{continuation_scene_id}"
            expected_supplemental_key = (
                f"{base_operation_key}:supplement:02"
            )
            base_attempt = (state.get("attempts") or {}).get(
                base_operation_key, {}
            )
            prior = ledger.get("prior_consumed_attempt") or {}
            try:
                reopened_prior = (
                    self._validate_exact_prior_consumed_pexels_attempt(
                        state=state,
                        operation_key=base_operation_key,
                        ledger=base_attempt,
                        version_id=uuid.UUID(
                            str(
                                continuation[
                                    "prior_attempt_artifact_version_id"
                                ]
                            )
                        ),
                        expected_content_hash=str(
                            continuation["prior_attempt_content_hash"]
                        ),
                    )
                )
            except (
                KeyError,
                TypeError,
                ValueError,
                ValidationFailureError,
            ):
                reopened_prior = None
            try:
                reconstructed_base_request = self._pexels_request(
                    state,
                    authority,
                    continuation_scene_id,
                    Path(state["workspace"]),
                    operation_key=base_operation_key,
                )
                if (
                    continuation.get("prior_request_schema")
                    == "mr1.pexels-provider-request.v1"
                ):
                    reconstructed_prior_request = (
                        self._legacy_pexels_request_v1(
                            reconstructed_base_request
                        )
                    )
                else:
                    reconstructed_prior_request = (
                        reconstructed_base_request
                    )
                reconstructed_base_query = (
                    build_mr1_pexels_query_authority(
                        reconstructed_prior_request
                    )
                )
                expected_derivation = (
                    self._build_stock_search_intent_derivation(
                        authority=authority,
                        scene_id=continuation_scene_id,
                        stock_search_intent=str(
                            continuation[
                                "approved_stock_search_intent"
                            ]
                        ),
                        request=request,
                    )
                )
                expected_query_diff = (
                    self._stock_search_query_material_diff(
                        base_query_authority=(
                            reconstructed_base_query
                        ),
                        approved_query_authority=deepcopy(
                            continuation[
                                "approved_query_authority"
                            ]
                        ),
                    )
                )
            except (
                KeyError,
                TypeError,
                ValueError,
                ValidationFailureError,
            ):
                reconstructed_prior_request = None
                reconstructed_base_query = None
                expected_derivation = None
                expected_query_diff = None
            base_query_evidence = (
                continuation.get("base_query_evidence") or {}
            )
            stock_search_authority_exact = bool(
                reconstructed_prior_request is not None
                and reconstructed_base_query is not None
                and expected_derivation is not None
                and expected_query_diff is not None
                and continuation.get("package_semantic_intent")
                == request.get("semantic_intent")
                == reconstructed_prior_request.get("semantic_intent")
                and continuation.get("approved_stock_search_intent")
                == request.get("stock_search_intent")
                and request.get("stock_search_intent_scope")
                == "PEXELS_SUPPORTING_SUBWINDOW_CONTEXT_ONLY"
                and continuation.get("prior_request_hash")
                == reconstructed_prior_request.get("request_hash")
                == base_query_evidence.get("request_hash")
                and continuation.get("stock_search_intent_derivation")
                == expected_derivation
                and continuation.get("query_material_diff")
                == expected_query_diff
                and base_query_evidence.get("query_authority")
                == reconstructed_base_query
                and base_query_evidence.get("content_hash")
                == content_hash(
                    {
                        key: value
                        for key, value in base_query_evidence.items()
                        if key != "content_hash"
                    }
                )
                and expected_query_diff.get("materially_different")
                is True
            )
            review_manifest_exact = bool(
                review_manifest_version is not None
                and self._provider_continuation_review_manifest_exact(
                    manifest=deepcopy(review_manifest_version.content or {}),
                    continuation=continuation,
                )
            )
            review_task_exact = bool(
                review_manifest_version is not None
                and reopened_prior is not None
                and self._provider_continuation_review_task_exact(
                    task=continuation_review_task,
                    state=state,
                    review_manifest_version=review_manifest_version,
                    prior_consumed_snapshot=reopened_prior,
                    expected_operator_id=(
                        approval.decided_by_user_id
                        if approval is not None
                        else uuid.UUID(int=0)
                    ),
                    expected_approval_decision_id=(
                        continuation_decision.id
                        if continuation_decision is not None
                        else None
                    ),
                    require_completed=True,
                )
            )
            decision_exact = bool(
                review_manifest_version is not None
                and continuation_review_task is not None
                and self._provider_continuation_decision_exact(
                    decision=continuation_decision,
                    state=state,
                    authorization_scope=authorization_scope,
                    authorization_hash=authorization_hash,
                    review_manifest_version=review_manifest_version,
                    expected_operator_id=(
                        approval.decided_by_user_id
                        if approval is not None
                        else uuid.UUID(int=0)
                    ),
                    expected_review_task_id=continuation_review_task.id,
                )
            )
            continuation_authority_valid = bool(
                continuation_scene_id in {"SC-04", "SC-07", "SC-09"}
                and operation_key == expected_supplemental_key
                and ledger.get("provider_attempt_ordinal") == 2
                and decision_exact
                and review_task_exact
                and review_manifest_exact
                and stock_search_authority_exact
                and authorization_hash
                == continuation.get("authorization_content_hash")
                == idempotency_authority_hash
                and continuation.get("run_id") == state["run_id"]
                and continuation.get("operation_key") == base_operation_key
                and continuation.get("additional_attempts") == 1
                and continuation.get("maximum_total_attempts") == 2
                and continuation.get("automatic_retry_allowed") is False
                and continuation.get("provider_substitution_allowed") is False
                and continuation.get("automatic_pexels_to_ai_fallback") is False
                and continuation.get("incremental_cost_cap_usd") == 0.0
                and request.get("semantic_intent")
                == continuation.get("package_semantic_intent")
                and request.get("stock_search_intent")
                == continuation.get("approved_stock_search_intent")
                and request.get("approved_query_authority")
                == continuation.get("approved_query_authority")
                and continuation.get("request_invariants_hash")
                == content_hash(request_invariants)
                and continuation.get("receipt_content_hash")
                == content_hash(receipt_without_hash)
                and request.get("provider_attempt_continuation_approval_id")
                == continuation.get("approval_decision_id")
                and request.get("provider_attempt_continuation_authorization_hash")
                == continuation.get("authorization_content_hash")
                and request.get("provider_attempt_ordinal") == 2
                and base_attempt.get("state") == "CONSUMED_FAILED"
                and base_attempt.get("submit_state") == "FAILED_CONSUMED"
                and base_attempt.get("attempt_count")
                == base_attempt.get("attempt_cap")
                == 1
                and base_attempt.get("network_submit_started") is True
                and base_attempt.get("search_submit_count") == 1
                and base_attempt.get("download_submit_count") == 0
                and base_attempt.get("request_hash")
                == continuation.get("prior_request_hash")
                and base_attempt.get("failure")
                == continuation.get("prior_failure")
                and reopened_prior == continuation.get("prior_consumed_attempt")
                and prior.get("artifact_version_id")
                == continuation.get("prior_attempt_artifact_version_id")
                and prior.get("content_hash")
                == continuation.get("prior_attempt_content_hash")
                and prior == reopened_prior
            )
        query_amendment = ledger.get("provider_query_amendment")
        query_amendment_authority_valid = query_amendment is None
        query_amendment_detail_checks: dict[str, bool] = {}
        if isinstance(query_amendment, dict):
            try:
                amendment_decision = self.session.get(
                    ApprovalDecision,
                    uuid.UUID(str(query_amendment["approval_decision_id"])),
                    populate_existing=True,
                    with_for_update=True,
                )
                amendment_review_task = self.session.get(
                    ReviewTask,
                    uuid.UUID(str(query_amendment["operator_review_task_id"])),
                    populate_existing=True,
                    with_for_update=True,
                )
                amendment_review_manifest = self._exact_version(
                    uuid.UUID(
                        str(
                            query_amendment[
                                "operator_review_manifest_artifact_version_id"
                            ]
                        )
                    ),
                    str(
                        query_amendment[
                            "operator_review_manifest_content_hash"
                        ]
                    ),
                    PROVIDER_CONTINUATION_REVIEW_ARTIFACT_TYPE,
                    uuid.UUID(state["project_id"]),
                    fresh_lock=True,
                )
            except (
                KeyError,
                TypeError,
                ValueError,
                ValidationFailureError,
            ):
                amendment_decision = None
                amendment_review_task = None
                amendment_review_manifest = None
            amendment_scene_id = str(ledger.get("scene_id") or "")
            amendment_spec = (
                query_amendment.get("pending_query_amendments") or {}
            ).get(amendment_scene_id)
            dynamic_request_fields = {
                "approval_id",
                "approval_content_hash",
                "idempotency_key",
                "idempotency_fingerprint",
                "destination",
                "request_hash",
                "provider_query_amendment_approval_id",
                "provider_query_amendment_authorization_hash",
                "excluded_provider_asset_ids",
            }
            amendment_request_invariants = {
                key: deepcopy(value)
                for key, value in request.items()
                if key not in dynamic_request_fields
            }
            amendment_receipt_without_hash = {
                key: value
                for key, value in query_amendment.items()
                if key != "receipt_content_hash"
            }
            amendment_authorization_scope = {
                key: value
                for key, value in query_amendment.items()
                if key
                not in {
                    "approval_decision_id",
                    "authorization_content_hash",
                    "decided_by_user_id",
                    "decided_at",
                    "receipt_content_hash",
                }
            }
            amendment_authorization_hash = content_hash(
                amendment_authorization_scope
            )
            try:
                amendment_base_query_payload = deepcopy(request)
                amendment_base_query_payload["stock_search_intent"] = (
                    amendment_base_query_payload["semantic_intent"]
                )
                amendment_base_query = build_mr1_pexels_query_authority(
                    amendment_base_query_payload
                )
                expected_amendment_derivation = (
                    self._build_stock_search_intent_derivation(
                        authority=authority,
                        scene_id=amendment_scene_id,
                        stock_search_intent=str(
                            amendment_spec[
                                "approved_stock_search_intent"
                            ]
                        ),
                        request=request,
                    )
                )
                expected_amendment_query_diff = (
                    self._stock_search_query_material_diff(
                        base_query_authority=amendment_base_query,
                        approved_query_authority=deepcopy(
                            amendment_spec[
                                "approved_query_authority"
                            ]
                        ),
                    )
                )
            except (
                KeyError,
                TypeError,
                ValueError,
                ValidationFailureError,
            ):
                amendment_base_query = None
                expected_amendment_derivation = None
                expected_amendment_query_diff = None
            amendment_base_query_evidence = (
                amendment_spec.get("base_query_evidence") or {}
                if isinstance(amendment_spec, dict)
                else {}
            )
            amendment_stock_search_authority_exact = bool(
                isinstance(amendment_spec, dict)
                and amendment_base_query is not None
                and expected_amendment_derivation is not None
                and expected_amendment_query_diff is not None
                and request.get("semantic_intent")
                == amendment_spec.get("package_semantic_intent")
                and request.get("stock_search_intent")
                == amendment_spec.get("approved_stock_search_intent")
                and request.get("stock_search_intent_scope")
                == "PEXELS_SUPPORTING_SUBWINDOW_CONTEXT_ONLY"
                and amendment_spec.get("stock_search_intent_derivation")
                == expected_amendment_derivation
                and amendment_spec.get("query_material_diff")
                == expected_amendment_query_diff
                and amendment_base_query_evidence.get("query_authority")
                == amendment_base_query
                and amendment_base_query_evidence.get("content_hash")
                == content_hash(
                    {
                        key: value
                        for key, value in (
                            amendment_base_query_evidence.items()
                        )
                        if key != "content_hash"
                    }
                )
                and expected_amendment_query_diff.get(
                    "materially_different"
                )
                is True
            )
            amendment_base_operation_key = str(
                query_amendment.get("operation_key") or ""
            )
            amendment_base_attempt = (state.get("attempts") or {}).get(
                amendment_base_operation_key,
                {},
            )
            try:
                reopened_amendment_prior = (
                    self._validate_exact_prior_consumed_pexels_attempt(
                        state=state,
                        operation_key=amendment_base_operation_key,
                        ledger=amendment_base_attempt,
                        version_id=uuid.UUID(
                            str(
                                query_amendment[
                                    "prior_attempt_artifact_version_id"
                                ]
                            )
                        ),
                        expected_content_hash=str(
                            query_amendment["prior_attempt_content_hash"]
                        ),
                    )
                )
            except (
                KeyError,
                TypeError,
                ValueError,
                ValidationFailureError,
            ):
                reopened_amendment_prior = None
            amendment_review_task_exact = bool(
                amendment_review_manifest is not None
                and reopened_amendment_prior is not None
                and self._provider_continuation_review_task_exact(
                    task=amendment_review_task,
                    state=state,
                    review_manifest_version=amendment_review_manifest,
                    prior_consumed_snapshot=reopened_amendment_prior,
                    expected_operator_id=(
                        approval.decided_by_user_id
                        if approval is not None
                        else uuid.UUID(int=0)
                    ),
                    expected_approval_decision_id=(
                        amendment_decision.id
                        if amendment_decision is not None
                        else None
                    ),
                    require_completed=True,
                )
                and reopened_amendment_prior
                == query_amendment.get("prior_consumed_attempt")
            )
            amendment_decision_exact = bool(
                amendment_review_manifest is not None
                and amendment_review_task is not None
                and self._provider_continuation_decision_exact(
                    decision=amendment_decision,
                    state=state,
                    authorization_scope=amendment_authorization_scope,
                    authorization_hash=amendment_authorization_hash,
                    review_manifest_version=amendment_review_manifest,
                    expected_operator_id=(
                        approval.decided_by_user_id
                        if approval is not None
                        else uuid.UUID(int=0)
                    ),
                    expected_review_task_id=amendment_review_task.id,
                )
                and amendment_review_task_exact
                and self._provider_continuation_review_manifest_exact(
                    manifest=deepcopy(
                        amendment_review_manifest.content or {}
                    ),
                    continuation=query_amendment,
                )
            )
            expected_amendment_invariants = (
                amendment_spec.get("request_invariants")
                if isinstance(amendment_spec, dict)
                else {}
            )
            if expected_amendment_invariants != amendment_request_invariants:
                ledger["provider_query_amendment_diagnostics"] = {
                    "mismatched_invariant_keys": sorted(
                        {
                            *expected_amendment_invariants,
                            *amendment_request_invariants,
                        }
                        - {
                            key
                            for key in {
                                *expected_amendment_invariants,
                                *amendment_request_invariants,
                            }
                            if expected_amendment_invariants.get(key)
                            == amendment_request_invariants.get(key)
                        }
                    ),
                    "approved_invariants_hash": content_hash(
                        expected_amendment_invariants
                    ),
                    "runtime_invariants_hash": content_hash(
                        amendment_request_invariants
                    ),
                    "secret_values_exposed": False,
                }
            query_amendment_detail_checks = {
                "scene": (
                    amendment_scene_id in {"SC-04", "SC-07", "SC-09"}
                    and isinstance(amendment_spec, dict)
                    and operation_key == f"pexels:{amendment_scene_id}"
                    and amendment_spec.get("operation_key") == operation_key
                ),
                "attempt_unconsumed": (
                    ledger.get("state") == "PLANNED"
                    and ledger.get("submit_state") == "NOT_SUBMITTED"
                    and ledger.get("attempt_count") == 0
                    and ledger.get("search_submit_count") == 0
                    and ledger.get("download_submit_count") == 0
                    and ledger.get("network_submit_started") is False
                    and isinstance(amendment_spec, dict)
                    and amendment_spec.get("attempt_count_at_approval") == 0
                ),
                "decision": bool(
                    amendment_decision_exact
                    and amendment_stock_search_authority_exact
                ),
                "authorization_hash": bool(
                    amendment_authorization_hash
                    == query_amendment.get("authorization_content_hash")
                    == idempotency_authority_hash
                ),
                "run": query_amendment.get("run_id") == state["run_id"],
                "package_semantic_intent": bool(
                    isinstance(amendment_spec, dict)
                    and request.get("semantic_intent")
                    == amendment_spec.get("package_semantic_intent")
                ),
                "stock_search_intent": bool(
                    isinstance(amendment_spec, dict)
                    and request.get("stock_search_intent")
                    == amendment_spec.get("approved_stock_search_intent")
                ),
                "query_authority": bool(
                    isinstance(amendment_spec, dict)
                    and request.get("approved_query_authority")
                    == amendment_spec.get("approved_query_authority")
                ),
                "request_invariants": bool(
                    isinstance(amendment_spec, dict)
                    and amendment_spec.get("request_invariants")
                    == amendment_request_invariants
                ),
                "request_invariants_hash": bool(
                    isinstance(amendment_spec, dict)
                    and amendment_spec.get("request_invariants_hash")
                    == content_hash(amendment_request_invariants)
                ),
                "receipt_hash": (
                    query_amendment.get("receipt_content_hash")
                    == content_hash(amendment_receipt_without_hash)
                ),
                "request_approval": (
                    request.get("provider_query_amendment_approval_id")
                    == query_amendment.get("approval_decision_id")
                    and request.get(
                        "provider_query_amendment_authorization_hash"
                    )
                    == query_amendment.get("authorization_content_hash")
                ),
                "no_retry_or_substitution": bool(
                    isinstance(amendment_spec, dict)
                    and amendment_spec.get("automatic_retry_allowed") is False
                    and amendment_spec.get("provider_substitution_allowed")
                    is False
                ),
                "cross_scene_exclusion_policy": (
                    isinstance(amendment_spec, dict)
                    and amendment_spec.get("excluded_provider_asset_policy")
                    == "ALL_PRIOR_SUCCESSFUL_PEXELS_OUTPUTS"
                ),
            }
            query_amendment_authority_valid = all(
                query_amendment_detail_checks.values()
            )
        checks = {
            "canonical_approval_reopened": bool(
                approval is not None
                and approval.decision == "approved"
                and canonical.get("approval_id") == state["approval_id"]
                and canonical.get("approval_content_hash")
                == state["approval_content_hash"]
                and canonical.get("exact_target") == state["exact_target"]
            ),
            "canonical_attempt_ledger_reopened": bool(
                persisted_ledger is not None
                and persisted_content.get("operation_key") == operation_key
                and persisted_content.get("state") == ledger["state"]
                and (ledger["state"] == "PLANNED" or resumable_drive)
                and persisted_content.get("attempt_count") == ledger["attempt_count"]
                and persisted_content.get("approval_content_hash")
                == state["approval_content_hash"]
                and persisted_content.get("idempotency_fingerprint")
                == ledger.get("idempotency_fingerprint")
            ),
            "provider_attempt_continuation_authority": (continuation_authority_valid),
            "provider_query_amendment_authority": (
                query_amendment_authority_valid
            ),
            "exact_request_hash": request["request_hash"] == expected,
            "attempt_available": (
                ledger["attempt_count"] < ledger["attempt_cap"] or resumable_drive
            ),
            "idempotency_exact": request["idempotency_key"]
            == ledger["idempotency_key"],
            "idempotency_fingerprint_contract_exact": bool(
                ledger.get("idempotency_fingerprint_contract")
                == IDEMPOTENCY_FINGERPRINT_CONTRACT
                and ledger.get("idempotency_fingerprint_serialization")
                == IDEMPOTENCY_FINGERPRINT_SERIALIZATION
                and ledger.get("idempotency_fingerprint")
                == expected_idempotency_fingerprint
                and request.get("idempotency_fingerprint")
                == expected_idempotency_fingerprint
            ),
            "provider_boundary": request["provider"] == ledger["provider"],
            "provider_model_voice_binding": self._runtime_request_binding_valid(
                operation_key=operation_key,
                request=request,
                authority=authority,
                state=state,
            ),
            "cost_hard_cap": bool(
                0.0
                <= float(ledger["hard_cap_usd"])
                <= float(authority["cost_scope"].get("hard_cap") or 0)
                <= 1.0
                and float(ledger["hard_cap_usd"])
                == float(fresh_budget_evidence.get("reserved_amount_usd") or 0)
            ),
            "monthly_budget": bool(
                ledger.get("monthly_budget_state") == "PASS"
                and budget_binding.get("result") == "PASS"
                and fresh_budget_evidence.get("status")
                in {
                    "RESERVED",
                    "SUBMITTED",
                    "SETTLED_ACTUAL",
                    "SETTLED_CONSERVATIVE",
                }
                and fresh_budget_evidence.get("run_id") == state["run_id"]
                and fresh_budget_evidence.get("project_id") == state["project_id"]
                and fresh_budget_evidence.get("reservation_ref")
                == budget_binding.get("reservation_ref")
                and fresh_budget_evidence.get("request_hash")
                == budget_binding.get("reservation_request_hash")
                and Decimal(str(fresh_budget_evidence.get("reserved_amount_usd") or 0))
                == Decimal(str(ledger["hard_cap_usd"]))
            ),
            "sdk_retry_disabled": request["sdk_retry"] is False,
        }
        checks.update(
            {
                f"provider_query_amendment:{key}": value
                for key, value in query_amendment_detail_checks.items()
            }
        )
        failed = [key for key, value in checks.items() if not value]
        if failed:
            raise RuntimeError(
                "MR1_RUNTIME_SUBMIT_PREFLIGHT_FAILED:" + ",".join(sorted(failed))
            )
        ledger["monthly_budget_latest_evidence"] = fresh_budget_evidence
        ledger["monthly_budget_state"] = "PASS"
        state["monthly_budget_reservation"] = deepcopy(fresh_budget_evidence)
        evidence = {
            "operation_key": operation_key,
            "request_hash": request["request_hash"],
            "idempotency_fingerprint": expected_idempotency_fingerprint,
            "monthly_budget_evidence": deepcopy(fresh_budget_evidence),
            "checks": {key: "PASS" for key in checks},
            "result": "PASS",
            "checked_at": datetime.now(UTC).isoformat(),
        }
        state["runtime_submit_preflights"].append(evidence)
        path = Path(state["workspace"]) / "runtime_submit_preflights"
        path.mkdir(parents=True, exist_ok=True)
        sequence = len(state["runtime_submit_preflights"])
        _write_json_atomic(
            path / (f"{sequence:02d}-{operation_key.replace(':', '_')}.json"),
            evidence,
        )

    def _runtime_request_binding_valid(
        self,
        *,
        operation_key: str,
        request: dict[str, Any],
        authority: dict[str, Any],
        state: dict[str, Any],
    ) -> bool:
        if operation_key == "elevenlabs:narration":
            voice = authority["resolved"]["voice_policy"]
            identity = voice["content"]["voice_identity"]
            return bool(
                request.get("script_hash")
                == authority["resolved"]["script"]["content_hash"]
                and request.get("spoken_text_hash")
                == authority["resolved"]["spoken_text_normalized"]["content_hash"]
                and request.get("voice_policy_content_hash") == voice["content_hash"]
                and request.get("voice_id") == identity.get("voice_id")
                and request.get("model_id") == identity.get("model_id")
                and request.get("narration_locale") == "en-US"
            )
        if operation_key == "elevenlabs:forced_alignment":
            narration = state["provider_outputs"].get("narration") or {}
            return bool(
                request.get("spoken_text_hash")
                == authority["resolved"]["spoken_text_normalized"]["content_hash"]
                and request.get("audio_sha256")
                == (narration.get("audio_sha256") or narration.get("sha256"))
                and request.get("strict_token_coverage") == 1.0
                and request.get("estimated_timing_fallback_allowed") is False
            )
        if operation_key.startswith("pexels:"):
            scene_id = str(
                ((state.get("attempts") or {}).get(operation_key) or {}).get("scene_id")
                or operation_key.rsplit(":", 1)[-1]
            )
            decisions = authority["resolved"]["visual_source_decision_set"]["content"][
                "decisions"
            ]
            decision = next(
                (item for item in decisions if item.get("scene_id") == scene_id),
                None,
            )
            temporal = state.get("temporal_authority") or {}
            window = next(
                (
                    item
                    for item in temporal.get("scene_windows") or []
                    if item.get("scene_id") == scene_id
                ),
                None,
            )
            supporting = next(
                (
                    item
                    for item in temporal.get("supporting_visual_subwindows") or []
                    if item.get("scene_id") == scene_id
                ),
                None,
            )
            stock = (supporting or {}).get("stock_context") or {}
            native = (supporting or {}).get("native_explanation") or {}
            excluded_provider_asset_ids = sorted(
                {
                    str(
                        output.get("provider_asset_id")
                        or (output.get("selected_candidate") or {}).get(
                            "provider_asset_id"
                        )
                        or (output.get("selected_candidate") or {}).get("id")
                        or ""
                    )
                    for key, output in (state.get("provider_outputs") or {}).items()
                    if key.startswith("pexels:") and isinstance(output, dict)
                }
                - {""}
            )
            continuation = (
                (state.get("attempts") or {})
                .get(operation_key, {})
                .get("provider_attempt_continuation")
            )
            query_amendment = (
                (state.get("attempts") or {})
                .get(operation_key, {})
                .get("provider_query_amendment")
            )
            expected_semantic_intent = decision.get("semantic_intent")
            expected_stock_search_intent = expected_semantic_intent
            expected_query_authority = None
            if isinstance(continuation, dict):
                expected_stock_search_intent = continuation.get(
                    "approved_stock_search_intent"
                )
                expected_query_authority = continuation.get(
                    "approved_query_authority"
                )
            elif isinstance(query_amendment, dict):
                amendment = (
                    query_amendment.get("pending_query_amendments") or {}
                ).get(scene_id)
                if isinstance(amendment, dict):
                    expected_stock_search_intent = amendment.get(
                        "approved_stock_search_intent"
                    )
                    expected_query_authority = amendment.get(
                        "approved_query_authority"
                    )
            return bool(
                decision is not None
                and window is not None
                and supporting is not None
                and decision.get("preferred_source_route") == "PEXELS_VIDEO"
                and decision.get("provider") == "pexels_api"
                and request.get("scene_id") == scene_id
                and request.get("semantic_intent") == expected_semantic_intent
                and request.get("stock_search_intent")
                == expected_stock_search_intent
                and request.get("stock_search_intent_scope")
                == "PEXELS_SUPPORTING_SUBWINDOW_CONTEXT_ONLY"
                and (
                    not (
                        isinstance(continuation, dict)
                        or isinstance(query_amendment, dict)
                    )
                    or request.get("approved_query_authority")
                    == expected_query_authority
                )
                and request.get("semantic_fit_threshold")
                == (
                    (authority.get("frozen_channel_policy") or {})
                    .get("channel_scoped_policy", {})
                    .get("provider_usage_policy", {})
                    .get("pexels", {})
                    .get("semantic_fit_threshold")
                )
                and request.get("semantic_fit_threshold_authority")
                == (
                    "frozen_channel_policy.provider_usage_policy.pexels."
                    "semantic_fit_threshold"
                )
                and request.get("canonical_timeline_hash")
                == temporal.get("timeline_hash")
                and request.get("scene_start_ms") == window.get("start_ms")
                and request.get("scene_end_ms") == window.get("end_ms")
                and request.get("scene_duration_ms") == window.get("duration_ms")
                and request.get("supporting_visual_subwindows_hash")
                == temporal.get("supporting_visual_subwindows_hash")
                and request.get("stock_context_start_ms") == stock.get("start_ms")
                and request.get("stock_context_end_ms") == stock.get("end_ms")
                and request.get("stock_context_duration_ms") == stock.get("duration_ms")
                and request.get("native_explanation_start_ms") == native.get("start_ms")
                and request.get("native_explanation_end_ms") == native.get("end_ms")
                and request.get("native_explanation_duration_ms")
                == native.get("duration_ms")
                and request.get("native_mechanism")
                == supporting.get("native_mechanism")
                and request.get("supporting_subwindow_policy_ref")
                == supporting.get("policy_ref")
                and request.get("minimum_duration_seconds")
                == (int(stock.get("duration_ms") or 0) + 999) // 1000
                and request.get("timing_authority") == "CANONICAL_MEDIA_TIMELINE"
                and request.get("estimated_timing_fallback_used") is False
                and request.get("automatic_pexels_to_ai_fallback") is False
                and request.get("provider_substitution_allowed") is False
                and request.get("excluded_provider_asset_ids")
                == excluded_provider_asset_ids
            )
        if operation_key == "google_drive:archive":
            return bool(
                request.get("archive_identity") == state.get("archive_identity")
                and request.get("operation") == "archive"
                and request.get("provider") == "google_drive"
                and len(str(request.get("manifest_hash") or "")) == 64
                and state.get("not_publishable") is True
                and state.get("youtube_calls") == 0
            )
        return False

    def _reserve_durable_monthly_budget(
        self,
        *,
        authority: dict[str, Any],
        run_id: uuid.UUID,
    ) -> dict[str, Any]:
        approved_hard_cap = Decimal(str(authority["cost_scope"].get("hard_cap") or 0))
        fresh_plan = (authority.get("reuse_decision_manifest") or {}).get(
            "fresh_provider_call_plan"
        ) or {}
        fresh_elevenlabs_required = bool(
            int(fresh_plan.get("elevenlabs_narration", 1))
            or int(fresh_plan.get("elevenlabs_forced_alignment", 1))
        )
        hard_cap = approved_hard_cap if fresh_elevenlabs_required else Decimal("0")
        frozen_budget = (
            (authority.get("frozen_channel_policy") or {})
            .get("channel_scoped_policy", {})
            .get("budget_policy", {})
        )
        channel_cap = Decimal(str(frozen_budget.get("monthly_channel_budget") or 0))
        environment_cap = Decimal(str(self.settings.monthly_ai_budget_usd or 0))
        if self.settings.elevenlabs_monthly_cap_usd is None:
            raise ValidationFailureError("MR1_BUDGET_ELEVENLABS_MONTHLY_CAP_REQUIRED")
        if self.settings.stock_monthly_budget_usd is None:
            raise ValidationFailureError("MR1_BUDGET_STOCK_MONTHLY_CAP_REQUIRED")
        evidence = MR1MonthlyBudgetAuthority(self.session).reserve_run(
            run_id=run_id,
            project_id=uuid.UUID(str(authority["project_id"])),
            reservation_amount_usd=hard_cap,
            environment_cap_usd=environment_cap,
            company_cap_usd=environment_cap,
            channel_cap_usd=channel_cap,
            provider_allocations_usd={
                "elevenlabs": hard_cap,
                "pexels_api": Decimal("0"),
                "google_drive": Decimal("0"),
            },
            provider_caps_usd={
                "elevenlabs": Decimal(str(self.settings.elevenlabs_monthly_cap_usd)),
                "pexels_api": Decimal(str(self.settings.stock_monthly_budget_usd)),
                "google_drive": environment_cap,
            },
            provider_aliases={
                "elevenlabs": ["elevenlabs", "forced_alignment"],
                "pexels_api": ["pexels", "pexels_api"],
                "google_drive": ["google_drive"],
            },
        )
        if (
            evidence.get("run_id") != str(run_id)
            or evidence.get("project_id") != authority["project_id"]
            or evidence.get("status") != "RESERVED"
            or Decimal(str(evidence.get("reserved_amount_usd"))) != hard_cap
            or Decimal(
                str(
                    (evidence.get("provider_allocations_usd") or {}).get(
                        "elevenlabs", "-1"
                    )
                )
            )
            != hard_cap
        ):
            raise ValidationFailureError("MR1_DURABLE_BUDGET_RESERVATION_INVALID")
        return evidence

    @staticmethod
    def _durable_narration_audio_recoverable(state: dict[str, Any]) -> bool:
        if (
            state.get("current_state") != "BLOCKED_REQUIRES_NEW_MR1_APPROVAL"
            or state.get("blocker") != "elevenlabs:narration:POST_SUBMIT_FAILURE"
        ):
            return False
        ledger = (state.get("attempts") or {}).get("elevenlabs:narration", {})
        if (
            ledger.get("state") != "CONSUMED_FAILED"
            or ledger.get("attempt_count") != ledger.get("attempt_cap")
            or ledger.get("failure")
            != "ValueError:TEMPORAL_ALIGNMENT_AUDIO_BOUNDS_INVALID"
        ):
            return False
        workspace = Path(str(state.get("workspace") or ""))
        audio = workspace / "narration" / "narration.mp3"
        try:
            resolved_workspace = workspace.resolve(strict=True)
            resolved_audio = audio.resolve(strict=True)
        except (OSError, FileNotFoundError):
            return False
        return bool(
            resolved_audio.is_file()
            and resolved_workspace in resolved_audio.parents
            and resolved_audio.stat().st_size > 0
        )

    def _recover_consumed_narration_audio(
        self,
        *,
        run_artifact: Artifact,
        state: dict[str, Any],
        authority: dict[str, Any],
        actor_id: uuid.UUID,
    ) -> ArtifactVersion:
        """Materialize usable bytes from the consumed call without a retry.

        ElevenLabs audio was atomically persisted before the optional TTS
        timestamp seed parser rejected an audio-padding boundary.  Canonical
        timing still comes from the separately approved forced-alignment call.
        This recovery never performs provider I/O and keeps the original
        one-attempt count.
        """

        if not self._durable_narration_audio_recoverable(state):
            raise ValidationFailureError(
                "MR1_NARRATION_OFFLINE_RECOVERY_NOT_AUTHORIZED"
            )
        from app.services.pa1r import media_duration_seconds, probe_media

        workspace = Path(state["workspace"]).resolve(strict=True)
        audio = (workspace / "narration" / "narration.mp3").resolve(strict=True)
        ffprobe = str(
            getattr(self.local_continuation, "ffprobe", None)
            or "/opt/homebrew/bin/ffprobe"
        )
        probe = probe_media(audio, ffprobe=ffprobe)
        duration_ms = round(media_duration_seconds(probe) * 1000)
        audio_sha256 = _sha256_file(audio)
        if (
            duration_ms < 360_000
            or duration_ms > 720_000
            or probe.get("evidence_sha256") != audio_sha256
        ):
            raise ValidationFailureError("MR1_RECOVERED_NARRATION_AUDIO_NOT_USABLE")
        request = self._narration_request(state, authority, workspace)
        ledger = state["attempts"]["elevenlabs:narration"]
        if ledger.get("request_hash") != request["request_hash"]:
            raise ValidationFailureError(
                "MR1_RECOVERED_NARRATION_REQUEST_BINDING_CHANGED"
            )
        voice = authority["resolved"]["voice_policy"]["content"]
        output = {
            "schema_version": "mr1.elevenlabs-narration-recovered.v1",
            "provider": "elevenlabs",
            "operation": "narration",
            "request_hash": request["request_hash"],
            "provider_request_hash": None,
            "provider_request_id": None,
            "provider_request_id_availability": (
                "NOT_DURABLY_CAPTURED_AFTER_TIMESTAMP_PARSE_FAILURE"
            ),
            "voice_id": voice["voice_identity"]["voice_id"],
            "model_id": voice["voice_identity"]["model_id"],
            "voice_settings": deepcopy(voice["pacing_policy"]["settings"]),
            "normalized_text_hash": request["normalized_text_hash"],
            "spoken_text_artifact_version_id": request[
                "spoken_text_artifact_version_id"
            ],
            "audio_path": str(audio),
            "audio_asset_ref": f"file-sha256:{audio_sha256}",
            "audio_sha256": audio_sha256,
            "audio_size_bytes": audio.stat().st_size,
            "audio_duration_ms": duration_ms,
            "timing_seed": None,
            "timing_seed_status": ("PENDING_DERIVATION_FROM_APPROVED_FORCED_ALIGNMENT"),
            "usage_metadata": {"availability": "UNKNOWN_NOT_DURABLY_CAPTURED"},
            "provider_text_normalization": "off",
            "provider_call_made": True,
            "network_submit_count": 1,
            "sdk_retry": False,
            "actual_cost_usd": None,
            "offline_recovery_from_consumed_attempt": True,
            "recovery_evidence": {
                "original_failure": ledger["failure"],
                "actual_audio_bytes_present": True,
                "actual_audio_probe": _redact_volatile(probe),
                "forced_alignment_remains_required": True,
                "provider_calls_repeated": False,
            },
            "secret_values_exposed": False,
        }
        runtime_gate = self._narration_runtime_gate(authority, output)
        if runtime_gate["result"] != "PASS":
            raise ValidationFailureError("MR1_RECOVERED_NARRATION_RUNTIME_GATE_FAILED")
        state["provider_outputs"]["narration"] = output
        state["narration_runtime_gate"] = runtime_gate
        ledger["state"] = "SUCCEEDED"
        ledger["submit_state"] = "SUCCEEDED"
        ledger["recovered_failure"] = ledger.pop("failure")
        ledger["offline_recovery"] = deepcopy(output["recovery_evidence"])
        ledger["output"] = deepcopy(output)
        ledger["succeeded_at"] = datetime.now(UTC).isoformat()
        self._save_attempt(state, "elevenlabs:narration", actor_id)
        state.pop("blocker", None)
        state["current_state"] = "NARRATION_READY"
        state.setdefault("event_order", []).extend(
            [
                "NARRATION_DURABLE_AUDIO_OFFLINE_RECOVERED",
                "NARRATION_RUNTIME_HARD_GATE_PASS",
            ]
        )
        version = self._save_run(run_artifact, state, actor_id=actor_id)
        self._durable_boundary()
        return version

    @staticmethod
    def _bind_recovered_timing_seed_from_forced_alignment(
        *,
        state: dict[str, Any],
        alignment_output: dict[str, Any],
    ) -> None:
        narration = state["provider_outputs"]["narration"]
        forced = alignment_output.get("forced_alignment_evidence") or {}
        normalized = alignment_output.get("temporal_spoken_text_normalized") or {}
        characters = deepcopy(forced.get("characters") or [])
        if (
            forced.get("verification_status") != "PASS"
            or not characters
            or forced.get("missing_tokens")
            or forced.get("extra_words")
            or forced.get("audio_asset_ref") != narration.get("audio_asset_ref")
            or forced.get("audio_duration_ms") != narration.get("audio_duration_ms")
            or not normalized.get("source_text_hash")
            or not normalized.get("spoken_text_hash")
        ):
            raise ValidationFailureError(
                "MR1_FORCED_ALIGNMENT_CANNOT_RECOVER_TIMING_SEED"
            )
        payload = {
            "provider_key": "elevenlabs_forced_alignment_recovery",
            "provider_request_id": forced.get("provider_request_id"),
            "audio_asset_ref": narration["audio_asset_ref"],
            "audio_duration_ms": narration["audio_duration_ms"],
            "source_text_hash": normalized["source_text_hash"],
            "spoken_text_hash": normalized["spoken_text_hash"],
            "original_character_alignment": characters,
            "normalized_character_alignment": characters,
            "provider_model_id": narration["model_id"],
            "provider_voice_id": narration["voice_id"],
            "seed": None,
            "voice_settings": deepcopy(narration["voice_settings"]),
            "pronunciation_dictionary_refs": deepcopy(
                normalized.get("pronunciation_dictionary_refs") or []
            ),
            "response_metadata": {
                "tts_audio_from_consumed_approved_attempt": True,
                "tts_timestamp_response_not_durably_available": True,
                "timing_source": "APPROVED_FORCED_ALIGNMENT",
                "provider_fallback_used": False,
            },
            "timing_available": True,
            "timing_parse_warnings": [
                "TTS_TIMESTAMP_BOUNDS_REJECTED",
                "TIMING_SEED_DERIVED_FROM_APPROVED_FORCED_ALIGNMENT",
            ],
        }
        payload["content_hash"] = content_hash(payload)
        narration["timing_seed"] = payload
        narration["timing_seed_status"] = "RECOVERED_FROM_APPROVED_FORCED_ALIGNMENT"

    def _mark_budget_submitted_if_needed(self, state: dict[str, Any]) -> dict[str, Any]:
        current = deepcopy(state.get("monthly_budget_reservation") or {})
        reservation_ref = str(current.get("reservation_ref") or "")
        if not reservation_ref:
            raise ValidationFailureError("MR1_BUDGET_RESERVATION_REF_MISSING")
        authority = MR1MonthlyBudgetAuthority(self.session)
        if current.get("status") == "RESERVED":
            current = authority.mark_submitted(reservation_ref)
            state.setdefault("event_order", []).append(
                "MONTHLY_BUDGET_RESERVATION_SUBMITTED"
            )
        else:
            current = authority.inspect(reservation_ref)
        if current.get("status") not in {
            "SUBMITTED",
            "SETTLED_ACTUAL",
            "SETTLED_CONSERVATIVE",
        }:
            raise ValidationFailureError(
                "MR1_BUDGET_NOT_AVAILABLE_AT_PROVIDER_BOUNDARY"
            )
        state["monthly_budget_reservation"] = current
        return current

    def _settle_budget_consumed_failure(self, state: dict[str, Any]) -> dict[str, Any]:
        current = deepcopy(state.get("monthly_budget_reservation") or {})
        reservation_ref = str(current.get("reservation_ref") or "")
        if current.get("status") == "SETTLED_CONSERVATIVE":
            return current
        settled = MR1MonthlyBudgetAuthority(self.session).settle_consumed_failure(
            reservation_ref
        )
        state["monthly_budget_reservation"] = settled
        state.setdefault("event_order", []).append(
            "MONTHLY_BUDGET_SETTLED_CONSERVATIVE"
        )
        return settled

    def _settle_budget_actual_if_known(self, state: dict[str, Any]) -> dict[str, Any]:
        current = deepcopy(state.get("monthly_budget_reservation") or {})
        if current.get("status") == "SETTLED_ACTUAL":
            return current
        if current.get("status") != "SUBMITTED":
            return current
        outputs = state.get("provider_outputs") or {}
        pexels_scenes = self._state_pexels_scenes(state)
        required = [
            outputs.get("narration"),
            outputs.get("alignment"),
            *(outputs.get(f"pexels:{scene}") for scene in pexels_scenes),
        ]
        if any(not isinstance(item, dict) for item in required):
            return current
        actual_values = [
            0.0
            if item.get("immutable_output_reused") is True
            and (item.get("reuse_receipt") or {}).get("provider_call_made_in_fresh_run")
            is False
            else item.get("actual_cost_usd")
            for item in required
        ]
        if any(value is None or isinstance(value, bool) for value in actual_values):
            current["actual_cost_reconciliation"] = "PENDING_PROVIDER_ACTUAL"
            state["monthly_budget_reservation"] = current
            return current
        actuals = [Decimal(str(value)) for value in actual_values]
        if any(not value.is_finite() or value < 0 for value in actuals):
            raise ValidationFailureError("MR1_PROVIDER_ACTUAL_COST_INVALID")
        elevenlabs_actual = actuals[0] + actuals[1]
        pexels_actual = sum(actuals[2:], Decimal("0"))
        provider_actuals = {
            "elevenlabs": elevenlabs_actual,
            "pexels_api": pexels_actual,
            "google_drive": Decimal("0"),
        }
        settled = MR1MonthlyBudgetAuthority(self.session).settle_success(
            str(current["reservation_ref"]),
            actual_amount_usd=sum(provider_actuals.values(), Decimal("0")),
            provider_actuals_usd=provider_actuals,
        )
        state["monthly_budget_reservation"] = settled
        state.setdefault("event_order", []).append("MONTHLY_BUDGET_SETTLED_ACTUAL")
        return settled

    @staticmethod
    def _reuse_entry_map(authority: dict[str, Any]) -> dict[str, dict[str, Any]]:
        manifest = authority.get("reuse_decision_manifest") or {}
        return {
            str(item.get("output_key")): item
            for item in manifest.get("entries") or []
            if isinstance(item, dict) and item.get("output_key")
        }

    def _materialize_approved_reuse(
        self,
        *,
        authority: dict[str, Any],
        workspace: Path,
        run_id: uuid.UUID,
    ) -> dict[str, Any]:
        """Copy only hash-proven immutable outputs into the fresh run.

        The old approval, run, and attempt ledgers remain historical evidence.
        Fresh attempt ledgers are created below and record that no network submit
        occurred in this run.  Timeline/caption/visual temporal files are never
        copied by this method.
        """

        empty = {"provider_outputs": {}, "receipts": {}}
        if authority.get("package_variant") != SC04_PROJECT_TYPE:
            return empty
        manifest = authority.get("reuse_decision_manifest") or {}
        manifest_ref = authority.get("reuse_decision_manifest_ref") or {}
        allowed = list(manifest.get("reuse_allowed_output_keys") or [])
        if not allowed:
            return empty
        if (
            allowed
            not in (
                ["narration_audio"],
                ["narration_audio", "forced_alignment"],
            )
            or manifest.get("prior_output_reuse_count") != len(allowed)
            or manifest.get("fresh_temporal_compilation_required") is not True
            or manifest.get("fresh_caption_compilation_required") is not True
            or manifest.get("canonical_timeline_reuse_authorized") is not False
            or manifest.get("supporting_visual_subwindows_reuse_authorized")
            is not False
            or content_hash(manifest) != manifest_ref.get("content_hash")
        ):
            raise ValidationFailureError("MR1_REUSE_MANIFEST_RUNTIME_INVALID")

        entries = self._reuse_entry_map(authority)
        source_run = manifest.get("source_run") or {}
        try:
            source_workspace = Path(str(source_run["workspace"])).resolve(strict=True)
        except (KeyError, OSError):
            raise ValidationFailureError(
                "MR1_REUSE_SOURCE_WORKSPACE_RUNTIME_INVALID"
            ) from None
        if not source_workspace.is_dir() or source_workspace.is_symlink():
            raise ValidationFailureError("MR1_REUSE_SOURCE_WORKSPACE_RUNTIME_INVALID")

        def checked_source(
            raw_path: Any,
            *,
            expected_sha256: Any,
            expected_size: Any = None,
        ) -> Path:
            try:
                path = Path(str(raw_path)).resolve(strict=True)
            except OSError:
                raise ValidationFailureError(
                    "MR1_REUSE_SOURCE_FILE_MISSING_OR_TAMPERED"
                ) from None
            if (
                not path.is_file()
                or path.is_symlink()
                or source_workspace not in path.parents
                or _sha256_file(path) != expected_sha256
                or (
                    expected_size is not None
                    and path.stat().st_size != int(expected_size)
                )
            ):
                raise ValidationFailureError(
                    "MR1_REUSE_SOURCE_FILE_MISSING_OR_TAMPERED"
                )
            return path

        def checked_json(
            raw_path: Any,
            *,
            expected_file_sha256: Any,
            expected_content_hash: Any,
        ) -> tuple[Path, dict[str, Any]]:
            path = checked_source(
                raw_path,
                expected_sha256=expected_file_sha256,
            )
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                raise ValidationFailureError(
                    "MR1_REUSE_SOURCE_JSON_MISSING_OR_TAMPERED"
                ) from None
            if (
                not isinstance(payload, dict)
                or content_hash(payload) != expected_content_hash
            ):
                raise ValidationFailureError(
                    "MR1_REUSE_SOURCE_JSON_MISSING_OR_TAMPERED"
                )
            return path, payload

        outputs: dict[str, Any] = {}
        receipts: dict[str, Any] = {}
        reuse_dir = workspace / "reuse_evidence"
        reuse_dir.mkdir(parents=True, exist_ok=True)

        narration_entry = entries.get("narration_audio") or {}
        if "narration_audio" in allowed:
            if (
                narration_entry.get("classification") != "REUSE_VALID"
                or narration_entry.get("reuse_authorized") is not True
            ):
                raise ValidationFailureError(
                    "MR1_NARRATION_REUSE_NOT_EXACTLY_AUTHORIZED"
                )
            prior = narration_entry.get("prior_output_evidence") or {}
            provider_path, narration = checked_json(
                prior.get("provider_output_path"),
                expected_file_sha256=prior.get("provider_output_file_sha256"),
                expected_content_hash=prior.get("provider_output_content_hash"),
            )
            audio_source = checked_source(
                prior.get("audio_path"),
                expected_sha256=prior.get("audio_sha256"),
                expected_size=prior.get("audio_size_bytes"),
            )
            audio_destination = workspace / "narration" / "narration.mp3"
            audio_destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(audio_source, audio_destination)
            if _sha256_file(audio_destination) != prior.get(
                "audio_sha256"
            ) or audio_destination.stat().st_size != int(
                prior.get("audio_size_bytes") or -1
            ):
                raise ValidationFailureError(
                    "MR1_REUSED_NARRATION_COPY_VERIFICATION_FAILED"
                )
            source_output_copy = reuse_dir / "source-narration-output.json"
            shutil.copy2(provider_path, source_output_copy)
            if _sha256_file(source_output_copy) != prior.get(
                "provider_output_file_sha256"
            ):
                raise ValidationFailureError(
                    "MR1_REUSED_NARRATION_EVIDENCE_COPY_FAILED"
                )
            receipt = {
                "schema_version": "mr1.immutable-output-reuse-receipt.v1",
                "output_key": "narration_audio",
                "fresh_run_id": str(run_id),
                "source_run": deepcopy(source_run),
                "reuse_decision_manifest_ref": deepcopy(manifest_ref),
                "source_provider_output_content_hash": prior.get(
                    "provider_output_content_hash"
                ),
                "source_provider_output_file_sha256": prior.get(
                    "provider_output_file_sha256"
                ),
                "source_audio_sha256": prior.get("audio_sha256"),
                "source_audio_size_bytes": prior.get("audio_size_bytes"),
                "source_audio_duration_ms": prior.get("audio_duration_ms"),
                "fresh_materialized_path": str(audio_destination.resolve()),
                "fresh_materialized_sha256": _sha256_file(audio_destination),
                "fresh_materialized_size_bytes": audio_destination.stat().st_size,
                "provider_call_made_in_fresh_run": False,
                "network_submit_count_in_fresh_run": 0,
                "old_approval_authority_reused": False,
                "old_run_authority_reused": False,
                "old_attempt_ledger_authority_reused": False,
            }
            receipt["content_hash"] = content_hash(receipt)
            _write_json_atomic(reuse_dir / "narration-reuse-receipt.json", receipt)
            narration = deepcopy(narration)
            narration.update(
                {
                    "audio_path": str(audio_destination.resolve()),
                    "source_audio_path": str(audio_source),
                    "immutable_output_reused": True,
                    "provider_call_made_in_current_run": False,
                    "network_submit_count_in_current_run": 0,
                    "reuse_receipt": deepcopy(receipt),
                }
            )
            outputs["narration"] = narration
            receipts["narration_audio"] = receipt

        alignment_entry = entries.get("forced_alignment") or {}
        if "forced_alignment" in allowed:
            if (
                "narration" not in outputs
                or alignment_entry.get("classification") != "REUSE_VALID"
                or alignment_entry.get("reuse_authorized") is not True
            ):
                raise ValidationFailureError(
                    "MR1_ALIGNMENT_REUSE_NOT_EXACTLY_AUTHORIZED"
                )
            prior = alignment_entry.get("prior_output_evidence") or {}
            alignment_source, alignment = checked_json(
                prior.get("alignment_path"),
                expected_file_sha256=prior.get("alignment_file_sha256"),
                expected_content_hash=prior.get("provider_output_content_hash"),
            )
            provider_path, provider_alignment = checked_json(
                prior.get("provider_output_path"),
                expected_file_sha256=prior.get("provider_output_file_sha256"),
                expected_content_hash=prior.get("provider_output_content_hash"),
            )
            if provider_alignment != alignment:
                raise ValidationFailureError(
                    "MR1_REUSED_ALIGNMENT_DUAL_EVIDENCE_MISMATCH"
                )
            alignment_destination = workspace / "alignment" / "alignment.json"
            alignment_destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(alignment_source, alignment_destination)
            if _sha256_file(alignment_destination) != prior.get(
                "alignment_file_sha256"
            ):
                raise ValidationFailureError(
                    "MR1_REUSED_ALIGNMENT_COPY_VERIFICATION_FAILED"
                )
            source_output_copy = reuse_dir / "source-alignment-output.json"
            shutil.copy2(provider_path, source_output_copy)
            if _sha256_file(source_output_copy) != prior.get(
                "provider_output_file_sha256"
            ):
                raise ValidationFailureError(
                    "MR1_REUSED_ALIGNMENT_EVIDENCE_COPY_FAILED"
                )
            receipt = {
                "schema_version": "mr1.immutable-output-reuse-receipt.v1",
                "output_key": "forced_alignment",
                "fresh_run_id": str(run_id),
                "source_run": deepcopy(source_run),
                "reuse_decision_manifest_ref": deepcopy(manifest_ref),
                "source_provider_output_content_hash": prior.get(
                    "provider_output_content_hash"
                ),
                "source_alignment_file_sha256": prior.get("alignment_file_sha256"),
                "source_forced_alignment_content_hash": prior.get(
                    "forced_alignment_content_hash"
                ),
                "source_audio_sha256": prior.get("audio_sha256"),
                "fresh_materialized_path": str(alignment_destination.resolve()),
                "fresh_materialized_sha256": _sha256_file(alignment_destination),
                "provider_call_made_in_fresh_run": False,
                "network_submit_count_in_fresh_run": 0,
                "old_approval_authority_reused": False,
                "old_run_authority_reused": False,
                "old_attempt_ledger_authority_reused": False,
            }
            receipt["content_hash"] = content_hash(receipt)
            _write_json_atomic(reuse_dir / "alignment-reuse-receipt.json", receipt)
            alignment = deepcopy(alignment)
            alignment.update(
                {
                    "audio_path": outputs["narration"]["audio_path"],
                    "source_alignment_path": str(alignment_source),
                    "immutable_output_reused": True,
                    "provider_call_made_in_current_run": False,
                    "network_submit_count_in_current_run": 0,
                    "reuse_receipt": deepcopy(receipt),
                }
            )
            outputs["alignment"] = alignment
            receipts["forced_alignment"] = receipt

        if set(receipts) != set(allowed):
            raise ValidationFailureError("MR1_REUSE_MATERIALIZATION_INCOMPLETE")
        return {"provider_outputs": outputs, "receipts": receipts}

    @staticmethod
    def _seed_reused_attempts(
        *,
        attempts: dict[str, dict[str, Any]],
        materialization: dict[str, Any],
    ) -> None:
        outputs = materialization.get("provider_outputs") or {}
        receipts = materialization.get("receipts") or {}
        for output_key, operation_key, receipt_key in (
            ("narration", "elevenlabs:narration", "narration_audio"),
            (
                "alignment",
                "elevenlabs:forced_alignment",
                "forced_alignment",
            ),
        ):
            if output_key not in outputs:
                continue
            ledger = attempts[operation_key]
            ledger.update(
                {
                    "state": "SUCCEEDED",
                    "submit_state": "REUSED_IMMUTABLE_OUTPUT_NO_SUBMIT",
                    "attempt_count": 0,
                    "network_submit_started": False,
                    "request_hash": None,
                    "provider_call_made_in_current_run": False,
                    "network_submit_count_in_current_run": 0,
                    "immutable_output_reused": True,
                    "reuse_receipt": deepcopy(receipts[receipt_key]),
                    "output": deepcopy(outputs[output_key]),
                }
            )

    def _initial_attempts(
        self,
        run_id: uuid.UUID,
        authority: dict[str, Any],
        *,
        budget_reservation: dict[str, Any],
        review_round: int = 1,
    ) -> dict[str, dict[str, Any]]:
        visual_routes = self._visual_route_authority(authority)
        operations = [
            ("elevenlabs:narration", "elevenlabs", "narration", None),
            (
                "elevenlabs:forced_alignment",
                "forced_alignment",
                "forced_alignment",
                None,
            ),
            *[
                (
                    f"pexels:{scene}",
                    "pexels_api",
                    "supporting_asset_acquisition",
                    scene,
                )
                for scene in visual_routes.pexels_scenes
            ],
            ("google_drive:archive", "google_drive", "archive", None),
            *(
                [
                    (
                        MR1_DRIVE_FINALIZATION_OPERATION_KEY,
                        "google_drive",
                        "finalization_supplement",
                        None,
                    )
                ]
                if (authority.get("provider_attempt_scope") or {}).get(
                    "drive_phase_count"
                )
                == 2
                else []
            ),
        ]
        hard_cap = float(budget_reservation.get("reserved_amount_usd") or 0)
        attempts: dict[str, dict[str, Any]] = {}
        for key, provider, operation, scene_id in operations:
            budget_evidence = {
                "schema_version": "mr1.attempt-budget-reservation-binding.v1",
                "run_id": str(run_id),
                "operation_key": key,
                "provider": provider,
                "reservation_ref": budget_reservation["reservation_ref"],
                "reservation_request_hash": budget_reservation["request_hash"],
                "reservation_content_hash": budget_reservation["content_hash"],
                "reservation_status_at_plan": budget_reservation["status"],
                "reserved_hard_cap_usd": budget_reservation["reserved_amount_usd"],
                "result": "PASS",
            }
            budget_evidence["content_hash"] = content_hash(budget_evidence)
            attempts[key] = {
                "schema_version": "mr1.provider-attempt-ledger.v1",
                "run_id": str(run_id),
                "operation_key": key,
                "provider": provider,
                "operation": operation,
                "scene_id": scene_id,
                "attempt_cap": 1,
                "attempt_count": 0,
                "network_submit_started": False,
                "search_submit_count": 0,
                "download_submit_count": 0,
                "pre_submit_failures": 0,
                "state": "PLANNED",
                "submit_state": "NOT_SUBMITTED",
                "idempotency_key": (
                    mr1_drive_finalization_idempotency_key(
                        run_id=run_id,
                        review_round=review_round,
                    )
                    if key == MR1_DRIVE_FINALIZATION_OPERATION_KEY
                    else f"mr1:{run_id}:{key}"
                ),
                "idempotency_fingerprint": _idempotency_fingerprint(
                    approval_content_hash=authority["approval_content_hash"],
                    run_id=run_id,
                    provider=provider,
                    operation=operation,
                    scene_id=scene_id,
                ),
                "idempotency_fingerprint_contract": (IDEMPOTENCY_FINGERPRINT_CONTRACT),
                "idempotency_fingerprint_serialization": (
                    IDEMPOTENCY_FINGERPRINT_SERIALIZATION
                ),
                "approval_id": authority["approval_id"],
                "approval_content_hash": authority["approval_content_hash"],
                "hard_cap_usd": hard_cap,
                "monthly_budget_state": budget_evidence["result"],
                "monthly_budget_evidence": budget_evidence,
                "provider_substitution_allowed": False,
                "automatic_retry_allowed": False,
            }
            if key == MR1_DRIVE_FINALIZATION_OPERATION_KEY:
                attempts[key].update(
                    {
                        "state": "WAITING_HUMAN_PASS",
                        "submit_state": "NOT_SUBMITTED",
                        "review_round": review_round,
                        "drive_phase_authority": deepcopy(
                            authority["provider_attempt_scope"][
                                "drive_idempotency_phases"
                            ][1]
                        ),
                        "distinct_from_canonical_archive": True,
                    }
                )
            elif (
                key == "google_drive:archive"
                and (authority.get("provider_attempt_scope") or {}).get(
                    "drive_phase_count"
                )
                == 2
            ):
                attempts[key].update(
                    {
                        "drive_phase_authority": deepcopy(
                            authority["provider_attempt_scope"][
                                "drive_idempotency_phases"
                            ][0]
                        ),
                        "distinct_from_finalization_supplement": True,
                    }
                )
        return attempts

    def _monthly_budget_evidence(
        self,
        *,
        authority: dict[str, Any],
        run_id: uuid.UUID | str,
        provider: str,
        operation_key: str,
    ) -> dict[str, Any]:
        """Compute a current-period reservation from persisted cost events.

        Empty cost-event rows truthfully mean zero recorded spend; they are not a
        fabricated PASS.  The evidence records the exact query scope, caps and a
        conservative reservation of the entire approved per-video hard ceiling.
        """

        now = datetime.now(UTC)
        period_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        period_end = (
            period_start.replace(year=period_start.year + 1, month=1)
            if period_start.month == 12
            else period_start.replace(month=period_start.month + 1)
        )
        project = self.session.get(
            VideoProject, uuid.UUID(str(authority["project_id"]))
        )
        if project is None:
            raise ValidationFailureError("MR1_BUDGET_PROJECT_AUTHORITY_MISSING")
        scope_ids = {
            project.id,
            project.company_id,
            project.channel_workspace_id,
        }
        base_filters = (
            CostEvent.created_at >= period_start,
            CostEvent.created_at < period_end,
            CostEvent.currency == "USD",
            CostEvent.cost_type.in_(["ACTUAL", "ADJUSTED", "REFUNDED"]),
            CostEvent.cost_scope_id.in_(scope_ids),
        )
        global_spend = Decimal(
            str(
                self.session.scalar(
                    select(func.coalesce(func.sum(CostEvent.amount), 0)).where(
                        *base_filters
                    )
                )
                or 0
            )
        )
        provider_aliases = {
            "elevenlabs": ("elevenlabs",),
            "forced_alignment": ("elevenlabs", "forced_alignment"),
            "pexels_api": ("pexels_api", "pexels"),
            "google_drive": ("google_drive",),
        }[provider]
        provider_spend = Decimal(
            str(
                self.session.scalar(
                    select(func.coalesce(func.sum(CostEvent.amount), 0)).where(
                        *base_filters,
                        CostEvent.provider_key.in_(provider_aliases),
                    )
                )
                or 0
            )
        )
        line_items = (
            authority["resolved"]["cost_estimate_snapshot"]["content"].get("line_items")
            or []
        )
        incremental = sum(
            (
                Decimal(str(item.get("estimated_incremental_cost_usd") or 0))
                for item in line_items
                if item.get("provider") == provider
            ),
            Decimal("0"),
        )
        frozen_budget = (
            (authority.get("frozen_channel_policy") or {})
            .get("channel_scoped_policy", {})
            .get("budget_policy", {})
        )
        authority_global_cap = Decimal(
            str(frozen_budget.get("monthly_channel_budget") or 0)
        )
        environment_global_cap = Decimal(str(self.settings.monthly_ai_budget_usd or 0))
        provider_cap: Decimal | None
        if provider in {"elevenlabs", "forced_alignment"}:
            provider_cap = (
                Decimal(str(self.settings.elevenlabs_monthly_cap_usd))
                if self.settings.elevenlabs_monthly_cap_usd is not None
                else None
            )
        elif provider == "pexels_api":
            provider_cap = (
                Decimal(str(self.settings.stock_monthly_budget_usd))
                if self.settings.stock_monthly_budget_usd is not None
                else None
            )
        else:
            provider_cap = None
        approved_hard_cap = Decimal(str(authority["cost_scope"].get("hard_cap") or 0))
        fresh_plan = (authority.get("reuse_decision_manifest") or {}).get(
            "fresh_provider_call_plan"
        ) or {}
        fresh_elevenlabs_required = bool(
            int(fresh_plan.get("elevenlabs_narration", 1))
            or int(fresh_plan.get("elevenlabs_forced_alignment", 1))
        )
        provider_reused = bool(
            (
                provider == "elevenlabs"
                and int(fresh_plan.get("elevenlabs_narration", 1)) == 0
            )
            or (
                provider == "forced_alignment"
                and int(fresh_plan.get("elevenlabs_forced_alignment", 1)) == 0
            )
        )
        if provider_reused:
            incremental = Decimal("0")
        hard_cap = approved_hard_cap if fresh_elevenlabs_required else Decimal("0")
        package_approved_estimate = Decimal(
            str(authority["cost_scope"].get("estimated_cost") or 0)
        )
        approved_estimate = (
            package_approved_estimate if fresh_elevenlabs_required else Decimal("0")
        )
        effective_global_cap = min(authority_global_cap, environment_global_cap)
        global_projected = global_spend + hard_cap
        provider_projected = provider_spend + incremental
        checks = {
            "currency_usd": authority["cost_scope"].get("currency") == "USD",
            "hard_cap_exact": bool(
                approved_hard_cap == Decimal("1.0")
                and hard_cap
                == (approved_hard_cap if fresh_elevenlabs_required else Decimal("0"))
            ),
            "estimate_within_hard_cap": approved_estimate <= hard_cap,
            "authority_global_cap_configured": authority_global_cap > 0,
            "environment_global_cap_covers_authority": (
                environment_global_cap >= authority_global_cap
            ),
            "global_reservation_within_cap": (global_projected <= effective_global_cap),
            "provider_budget_configured_or_not_applicable": (
                provider_cap is not None
                if provider in {"elevenlabs", "forced_alignment", "pexels_api"}
                else True
            ),
            "provider_projection_within_cap": (
                provider_cap is None or provider_projected <= provider_cap
            ),
        }
        failed = sorted(key for key, value in checks.items() if value is not True)
        evidence = {
            "schema_version": "mr1.monthly-budget-reservation.v1",
            "run_id": str(run_id),
            "operation_key": operation_key,
            "provider": provider,
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
            "cost_event_scope_ids": sorted(str(value) for value in scope_ids),
            "cost_event_types": ["ACTUAL", "ADJUSTED", "REFUNDED"],
            "recorded_global_spend_usd": float(global_spend),
            "recorded_provider_spend_usd": float(provider_spend),
            "approved_incremental_estimate_usd": float(incremental),
            "package_approved_estimate_usd": float(package_approved_estimate),
            "fresh_run_approved_estimate_usd": float(approved_estimate),
            "immutable_provider_output_reused": provider_reused,
            "reserved_hard_cap_usd": float(hard_cap),
            "approved_package_hard_cap_usd": float(approved_hard_cap),
            "fresh_elevenlabs_execution_required": fresh_elevenlabs_required,
            "old_provider_cost_reused_or_resettled": False,
            "authority_global_cap_usd": float(authority_global_cap),
            "environment_global_cap_usd": float(environment_global_cap),
            "effective_global_cap_usd": float(effective_global_cap),
            "provider_cap_usd": (
                float(provider_cap) if provider_cap is not None else None
            ),
            "global_projected_usd": float(global_projected),
            "provider_projected_usd": float(provider_projected),
            "reservation_ref": f"mr1-budget://{run_id}/{operation_key}",
            "checks": {
                key: "PASS" if value else "FAIL"
                for key, value in sorted(checks.items())
            },
            "failed_checks": failed,
            "result": "PASS" if not failed else "FAIL",
            "checked_at": now.isoformat(),
        }
        evidence["content_hash"] = content_hash(
            {key: value for key, value in evidence.items() if key != "content_hash"}
        )
        return evidence

    @staticmethod
    def _provider_continuation_operator_text(
        *,
        scene_id: str,
        pending_scene_ids: list[str],
        review_manifest_content_hash: str,
    ) -> str:
        amendment_text = "".join(
            f" và đổi query Pexels {pending_scene_id} trước attempt đầu tiên"
            for pending_scene_id in pending_scene_ids
        )
        return (
            f"Phê duyệt thêm đúng 1 Pexels {scene_id} attempt"
            f"{amendment_text} cho run này; manifest sha256 "
            f"{review_manifest_content_hash}"
        )

    def _reopen_package_bound_artifact_version(
        self,
        *,
        authority: dict[str, Any],
        artifact_type: str,
    ) -> ArtifactVersion:
        package = authority.get("package") or {}
        candidates: list[dict[str, Any]] = []
        for section_name in (
            "effective_artifacts",
            "revised_artifacts",
            "reused_artifacts",
        ):
            section = package.get(section_name) or {}
            ref = section.get(artifact_type)
            if isinstance(ref, dict):
                candidates.append(ref)
        unique_candidates = {
            content_hash(candidate): candidate for candidate in candidates
        }
        if len(unique_candidates) != 1:
            raise ValidationFailureError(
                f"MR1_STOCK_SEARCH_{artifact_type.upper()}_REF_INVALID"
            )
        ref = next(iter(unique_candidates.values()))
        try:
            version_id = uuid.UUID(
                str(ref["artifact_version_id"])
            )
            allowed_project_ids = {
                uuid.UUID(str(authority["project_id"]))
            }
            raw_authority_projects = (
                authority.get("authority_project_ids") or {}
            )
            if isinstance(raw_authority_projects, dict):
                allowed_project_ids.update(
                    uuid.UUID(str(value))
                    for value in raw_authority_projects.values()
                )
            for binding in (
                (package.get("exact_bindings") or {}).values()
            ):
                ref_value = (
                    binding.get("ref")
                    if isinstance(binding, dict)
                    else None
                )
                if isinstance(ref_value, str) and ref_value.startswith(
                    "video-project://"
                ):
                    allowed_project_ids.add(
                        uuid.UUID(
                            ref_value.removeprefix(
                                "video-project://"
                            )
                        )
                    )
        except (KeyError, TypeError, ValueError):
            raise ValidationFailureError(
                f"MR1_STOCK_SEARCH_{artifact_type.upper()}_REF_INVALID"
            ) from None
        version = self.session.get(ArtifactVersion, version_id)
        artifact = (
            self.session.get(Artifact, version.artifact_id)
            if version is not None
            else None
        )
        resolved = (authority.get("resolved") or {}).get(
            artifact_type
        )
        checks = {
            "version_present": version is not None,
            "artifact_present": artifact is not None,
            "artifact_type": (
                artifact is not None
                and artifact.artifact_type == artifact_type
            ),
            "project_scope": (
                artifact is not None
                and artifact.video_project_id in allowed_project_ids
            ),
            "artifact_status": (
                artifact is not None
                and artifact.status in {"in_review", "approved"}
            ),
            "version_status": (
                version is not None
                and version.status
                in {"submitted", "approved", "superseded"}
            ),
            "artifact_id": (
                version is not None
                and str(version.artifact_id) == ref.get("artifact_id")
            ),
            "artifact_version_ref": (
                version is not None
                and ref.get("artifact_version_ref")
                == f"artifact-version://{version.id}"
            ),
            "version_number": (
                version is not None
                and version.version_number == ref.get("version_number")
            ),
            "content_hash_ref": (
                version is not None
                and version.content_hash == ref.get("content_hash")
            ),
            "content_hash_recomputed": (
                version is not None
                and content_hash(version.content or {})
                == version.content_hash
            ),
            "resolved_binding": (
                not isinstance(resolved, dict)
                or (
                    version is not None
                    and resolved.get("artifact_id")
                    == str(version.artifact_id)
                    and resolved.get("artifact_version_id")
                    == str(version.id)
                    and resolved.get("content_hash")
                    == version.content_hash
                    and resolved.get("content") == version.content
                )
            ),
        }
        failed_checks = sorted(
            key for key, passed in checks.items() if passed is not True
        )
        if failed_checks:
            raise ValidationFailureError(
                f"MR1_STOCK_SEARCH_{artifact_type.upper()}_REF_INVALID:"
                + ",".join(failed_checks)
            )
        return version

    def _build_stock_search_intent_derivation(
        self,
        *,
        authority: dict[str, Any],
        scene_id: str,
        stock_search_intent: str,
        request: dict[str, Any],
    ) -> dict[str, Any]:
        visual_source_version = (
            self._reopen_package_bound_artifact_version(
                authority=authority,
                artifact_type="visual_source_decision_set",
            )
        )
        script_version = self._reopen_package_bound_artifact_version(
            authority=authority,
            artifact_type="script",
        )
        scene_visual_version = (
            self._reopen_package_bound_artifact_version(
                authority=authority,
                artifact_type="scene_visual_intent",
            )
        )
        decisions = (visual_source_version.content or {}).get(
            "decisions"
        ) or []
        scene_visuals = (scene_visual_version.content or {}).get(
            "scenes"
        ) or []
        script_segments = (script_version.content or {}).get(
            "segments"
        ) or []
        visual_source_matches = [
            (index, item)
            for index, item in enumerate(decisions)
            if isinstance(item, dict) and item.get("scene_id") == scene_id
        ]
        scene_visual_matches = [
            (index, item)
            for index, item in enumerate(scene_visuals)
            if isinstance(item, dict) and item.get("scene_id") == scene_id
        ]
        script_segment_id = f"S{scene_id.removeprefix('SC-')}"
        script_matches = [
            (index, item)
            for index, item in enumerate(script_segments)
            if isinstance(item, dict)
            and item.get("segment_id") in {scene_id, script_segment_id}
        ]
        if (
            len(visual_source_matches) != 1
            or len(scene_visual_matches) != 1
            or len(script_matches) != 1
        ):
            raise ValidationFailureError(
                "MR1_STOCK_SEARCH_DERIVATION_SOURCE_AMBIGUOUS"
            )
        visual_source_index, visual_source_decision = (
            visual_source_matches[0]
        )
        scene_visual_index, scene_visual = scene_visual_matches[0]
        script_index, script_segment = script_matches[0]
        package_semantic_intent = str(
            visual_source_decision.get("semantic_intent") or ""
        ).strip()
        scene_visual_semantic_intent = str(
            scene_visual.get("semantic_intent") or ""
        ).strip()
        script_visual_hint = str(
            script_segment.get("visual_intent_hint") or ""
        ).strip()
        script_text = str(script_segment.get("text") or "").strip()
        if (
            not package_semantic_intent
            or package_semantic_intent
            != scene_visual_semantic_intent
            or request.get("semantic_intent") != package_semantic_intent
            or request.get("stock_search_intent") != stock_search_intent
            or request.get("stock_search_intent_scope")
            != "PEXELS_SUPPORTING_SUBWINDOW_CONTEXT_ONLY"
            or visual_source_decision.get("preferred_source_route")
            != "PEXELS_VIDEO"
            or visual_source_decision.get("provider") != "pexels_api"
            or not script_visual_hint
            or not script_text
        ):
            raise ValidationFailureError(
                "MR1_STOCK_SEARCH_PACKAGE_SEMANTIC_BINDING_INVALID"
            )
        derivation = {
            "schema_version": (
                "mr1.pexels-observable-subintent-derivation.v1"
            ),
            "scene_id": scene_id,
            "scope": "PEXELS_SUPPORTING_SUBWINDOW_CONTEXT_ONLY",
            "package_semantic_intent": package_semantic_intent,
            "approved_stock_search_intent": stock_search_intent,
            "route": "PEXELS_VIDEO",
            "source_role": "PEXELS_SUPPORTING",
            "evidence_role": "CONTEXT_ONLY",
            "package_semantic_intent_unchanged": True,
            "native_explanation_unchanged": True,
            "refs": {
                "visual_source_decision": {
                    "artifact_version_id": str(
                        visual_source_version.id
                    ),
                    "content_hash": visual_source_version.content_hash,
                    "json_pointer": (
                        f"/decisions/{visual_source_index}"
                    ),
                },
                "scene_visual_intent": {
                    "artifact_version_id": str(scene_visual_version.id),
                    "content_hash": scene_visual_version.content_hash,
                    "json_pointer": f"/scenes/{scene_visual_index}",
                },
                "script": {
                    "artifact_version_id": str(script_version.id),
                    "content_hash": script_version.content_hash,
                    "json_pointer": f"/segments/{script_index}",
                    "segment_id": script_segment["segment_id"],
                    "segment_text_hash": content_hash(
                        {
                            "segment_id": script_segment["segment_id"],
                            "text": script_text,
                        }
                    ),
                },
                "supporting_subwindow": {
                    "canonical_timeline_hash": request[
                        "canonical_timeline_hash"
                    ],
                    "supporting_visual_subwindows_hash": request[
                        "supporting_visual_subwindows_hash"
                    ],
                    "stock_context_start_ms": request[
                        "stock_context_start_ms"
                    ],
                    "stock_context_end_ms": request[
                        "stock_context_end_ms"
                    ],
                    "stock_context_duration_ms": request[
                        "stock_context_duration_ms"
                    ],
                    "native_explanation_start_ms": request[
                        "native_explanation_start_ms"
                    ],
                    "native_explanation_end_ms": request[
                        "native_explanation_end_ms"
                    ],
                    "native_explanation_duration_ms": request[
                        "native_explanation_duration_ms"
                    ],
                    "native_mechanism": request["native_mechanism"],
                    "policy_ref": request[
                        "supporting_subwindow_policy_ref"
                    ],
                },
            },
            "source_context": {
                "visual_source_semantic_intent": (
                    package_semantic_intent
                ),
                "scene_visual_semantic_intent": (
                    scene_visual_semantic_intent
                ),
                "script_visual_intent_hint": script_visual_hint,
                "script_segment_text": script_text,
            },
            "derivation_evidence": [
                {
                    "source_phrase": package_semantic_intent,
                    "observable_paraphrase": stock_search_intent,
                    "relationship": (
                        "BOUNDED_STOCK_SEARCH_PARAPHRASE_"
                        "REQUIRES_EXACT_HUMAN_REVIEW"
                    ),
                },
                {
                    "source_phrase": script_visual_hint,
                    "observable_paraphrase": stock_search_intent,
                    "relationship": (
                        "SCRIPT_VISUAL_HINT_TO_CONTEXT_ONLY_STOCK_SEARCH_"
                        "REQUIRES_EXACT_HUMAN_REVIEW"
                    ),
                },
                {
                    "source_phrase": script_text,
                    "observable_paraphrase": stock_search_intent,
                    "relationship": (
                        "SCRIPT_SEGMENT_CONTEXT_TO_OBSERVABLE_STOCK_SEARCH_"
                        "REQUIRES_EXACT_HUMAN_REVIEW"
                    ),
                },
            ],
            "automatic_semantic_rewrite_authorized": False,
            "exact_human_review_required": True,
        }
        derivation["content_hash"] = content_hash(derivation)
        return derivation

    @staticmethod
    def _stock_search_query_material_diff(
        *,
        base_query_authority: dict[str, Any],
        approved_query_authority: dict[str, Any],
    ) -> dict[str, Any]:
        payload = {
            "schema_version": "mr1.pexels-stock-search-query-diff.v1",
            "base_query_authority_hash": content_hash(
                base_query_authority
            ),
            "approved_query_authority_hash": content_hash(
                approved_query_authority
            ),
            "base_primary_query": base_query_authority["primary_query"],
            "approved_primary_query": approved_query_authority[
                "primary_query"
            ],
            "base_query_family": deepcopy(
                base_query_authority["query_family"]
            ),
            "approved_query_family": deepcopy(
                approved_query_authority["query_family"]
            ),
            "materially_different": (
                base_query_authority["primary_query"]
                != approved_query_authority["primary_query"]
            ),
        }
        payload["content_hash"] = content_hash(payload)
        return payload

    @staticmethod
    def _legacy_pexels_request_v1(
        request_v2: dict[str, Any],
    ) -> dict[str, Any]:
        """Reconstruct the exact pre-stock-subintent request shape.

        This is read-only migration evidence for an already-consumed ledger.
        It never rewrites that ledger or its immutable ArtifactVersion.
        """

        legacy_core = {
            key: deepcopy(value)
            for key, value in request_v2.items()
            if key
            not in {
                "request_hash",
                "stock_search_intent",
                "stock_search_intent_scope",
            }
        }
        return {
            **legacy_core,
            "request_hash": content_hash(legacy_core),
        }

    @staticmethod
    def _sanitized_pexels_failure_evidence(
        *,
        exc: Exception,
        workspace: Path,
    ) -> dict[str, Any] | None:
        safe_kind = getattr(exc, "safe_evidence_kind", None)
        raw = getattr(exc, "safe_evidence", None)
        allowlist = {
            "PEXELS_SEARCH_RANKING_FAILURE": {
                "schemas": {
                    "vcos.pexels-search-ranking-failure.v2",
                },
                "reason_code": "PEXELS_SEMANTIC_FIT_INADEQUATE",
                "guarded_key": "pexels_search_ranking_failure",
                "keys": {
                    "schema_version",
                    "reason_code",
                    "recorded_at",
                    "request_id",
                    "query_plan",
                    "retrieval_evidence",
                    "cross_scene_exclusion",
                    "technical_viability_filter",
                    "ranking",
                    "semantic_scoring_evidence",
                    "semantic_fit_gate",
                    "rate_limit",
                    "sanitization",
                    "content_hash",
                    "evidence_path",
                    "evidence_persisted",
                },
            },
            "PEXELS_DOWNLOAD_VIABILITY_FAILURE": {
                "schemas": {
                    "vcos.pexels-download-viability-failure.v1",
                },
                "reason_code": "PEXELS_NO_DOWNLOAD_VIABLE_CANDIDATES",
                "guarded_key": "pexels_download_viability_failure",
                "keys": {
                    "schema_version",
                    "reason_code",
                    "recorded_at",
                    "request_id",
                    "query_plan_hash",
                    "retrieval_evidence",
                    "cross_scene_exclusion",
                    "technical_viability_filter",
                    "sanitization",
                    "content_hash",
                    "evidence_path",
                    "evidence_persisted",
                },
            },
        }
        rule = allowlist.get(safe_kind)
        if rule is None or not isinstance(raw, dict):
            return None
        if (
            raw.get("schema_version") not in rule["schemas"]
            or raw.get("reason_code") != rule["reason_code"]
            or raw.get("evidence_persisted") is not True
            or set(raw) != rule["keys"]
        ):
            return None
        raw_content_hash = str(raw.get("content_hash") or "")
        persisted_payload_expected = {
            key: deepcopy(value)
            for key, value in raw.items()
            if key not in {"evidence_path", "evidence_persisted"}
        }
        provider_hash_payload = {
            key: deepcopy(value)
            for key, value in persisted_payload_expected.items()
            if key != "content_hash"
        }
        if (
            len(raw_content_hash) != 64
            or content_hash(provider_hash_payload) != raw_content_hash
        ):
            return None
        evidence_path_raw = raw.get("evidence_path")
        if not isinstance(evidence_path_raw, str):
            return None
        unresolved_evidence_path = Path(evidence_path_raw)
        if (
            unresolved_evidence_path.is_symlink()
            or unresolved_evidence_path.suffix != ".json"
        ):
            return None
        try:
            evidence_path = unresolved_evidence_path.resolve(strict=True)
            workspace_resolved = workspace.resolve(strict=True)
        except (OSError, RuntimeError):
            return None
        if (
            evidence_path != workspace_resolved
            and workspace_resolved not in evidence_path.parents
        ):
            return None
        if (
            not evidence_path.is_file()
            or evidence_path.stat().st_size > 5_000_000
            or any(
                parent.is_symlink()
                for parent in unresolved_evidence_path.parents
                if parent != workspace_resolved
            )
        ):
            return None
        try:
            persisted_payload = json.loads(
                evidence_path.read_text(encoding="utf-8")
            )
        except (OSError, UnicodeError, json.JSONDecodeError):
            return None
        if persisted_payload != persisted_payload_expected:
            return None
        sanitization = raw.get("sanitization")
        if not isinstance(sanitization, dict):
            return None
        expected_sanitization_keys = {
            "authorization_header_persisted",
            "api_key_persisted",
            "raw_provider_payload_persisted",
            "raw_media_urls_persisted",
            "secret_values_exposed",
        }
        if safe_kind == "PEXELS_SEARCH_RANKING_FAILURE":
            expected_sanitization_keys.add(
                "candidate_text_normalized_to_tokens"
            )
        else:
            expected_sanitization_keys.add("raw_query_persisted")
        if set(sanitization) != expected_sanitization_keys:
            return None
        required_false_flags = {
            "authorization_header_persisted",
            "api_key_persisted",
            "raw_provider_payload_persisted",
            "raw_media_urls_persisted",
            "secret_values_exposed",
        }
        if any(sanitization.get(key) is not False for key in required_false_flags):
            return None
        if safe_kind == "PEXELS_SEARCH_RANKING_FAILURE" and (
            sanitization.get("candidate_text_normalized_to_tokens")
            is not True
        ):
            return None
        if safe_kind == "PEXELS_DOWNLOAD_VIABILITY_FAILURE" and (
            sanitization.get("raw_query_persisted") is not False
        ):
            return None

        def contains_forbidden_transport_value(
            value: Any,
            *,
            field_name: str = "",
        ) -> bool:
            normalized_field = field_name.casefold().replace("-", "_")
            guarded_flag = normalized_field in {
                "authorization_header_persisted",
                "api_key_persisted",
                "raw_media_urls_persisted",
                "secret_values_exposed",
            }
            if guarded_flag:
                return value is not False
            if any(
                token in normalized_field
                for token in (
                    "authorization",
                    "api_key",
                    "secret",
                    "download_url",
                    "source_page_url",
                    "creator_url",
                    "media_url",
                )
            ):
                return value is not None and value is not False and value != ""
            if isinstance(value, str):
                lowered = value.casefold()
                return (
                    "http://" in lowered
                    or "https://" in lowered
                    or "authorization:" in lowered
                    or "api_key=" in lowered
                    or "apikey=" in lowered
                    or "access_token=" in lowered
                    or "bearer " in lowered
                    or "sk_live_" in lowered
                    or "sk_test_" in lowered
                    or "sk-" in lowered
                    or "aiza" in lowered
                    or "ya29." in lowered
                    or "ghp_" in lowered
                    or "github_pat_" in lowered
                    or "xoxb-" in lowered
                    or "xoxp-" in lowered
                    or "-----begin private key" in lowered
                    or "client_secret=" in lowered
                    or "password=" in lowered
                )
            if isinstance(value, dict):
                return any(
                    contains_forbidden_transport_value(
                        nested,
                        field_name=str(key),
                    )
                    for key, nested in value.items()
                )
            if isinstance(value, (list, tuple)):
                return any(
                    contains_forbidden_transport_value(item)
                    for item in value
                )
            return False

        if contains_forbidden_transport_value(persisted_payload_expected):
            return None
        semantic_gate = raw.get("semantic_fit_gate") or {}
        retrieval = raw.get("retrieval_evidence") or {}
        query_plan = raw.get("query_plan") or {}
        sanitized = {
            "schema_version": "mr1.pexels-safe-failure-evidence-ref.v1",
            "safe_evidence_kind": safe_kind,
            "guarded_key": rule["guarded_key"],
            "provider_evidence_schema_version": raw.get(
                "schema_version"
            ),
            "reason_code": raw.get("reason_code"),
            "provider_evidence_content_hash": raw_content_hash,
            "evidence_ref": (
                "workspace-relative://"
                + evidence_path.relative_to(workspace_resolved).as_posix()
            ),
            "evidence_file_sha256": _sha256_file(evidence_path),
            "evidence_persisted": raw.get("evidence_persisted") is True,
            "query_plan_hash": (
                query_plan.get("plan_hash")
                or raw.get("query_plan_hash")
            ),
            "provider_result_count": retrieval.get(
                "provider_result_count"
            ),
            "semantic_fit_gate": {
                "threshold": semantic_gate.get("threshold"),
                "selected_semantic_relevance": semantic_gate.get(
                    "selected_semantic_relevance"
                ),
                "highest_ranked_semantic_relevance": semantic_gate.get(
                    "highest_ranked_semantic_relevance"
                ),
                "result": semantic_gate.get("result"),
            },
            "raw_provider_payload_persisted": False,
            "raw_media_urls_persisted": False,
            "secret_values_exposed": False,
        }
        sanitized["content_hash"] = content_hash(sanitized)
        return sanitized

    @staticmethod
    def _sanitized_pexels_failure_evidence_ref_exact(
        *,
        safe_failure_evidence: dict[str, Any],
        workspace: Path,
    ) -> bool:
        """Reopen and re-sanitize the exact evidence bytes bound by a ledger.

        The provider failure JSON remains part of the local archive source set.
        A durable ref is therefore insufficient by itself: every continuation
        and the final Drive boundary must prove that the same regular JSON file
        still exists, has the same SHA-256, and still passes the allowlist.
        """

        if not isinstance(safe_failure_evidence, dict):
            return False
        safe_without_hash = {
            key: deepcopy(value)
            for key, value in safe_failure_evidence.items()
            if key != "content_hash"
        }
        if safe_failure_evidence.get("content_hash") != content_hash(
            safe_without_hash
        ):
            return False
        evidence_ref = safe_failure_evidence.get("evidence_ref")
        prefix = "workspace-relative://"
        if not isinstance(evidence_ref, str) or not evidence_ref.startswith(
            prefix
        ):
            return False
        relative = PurePosixPath(evidence_ref.removeprefix(prefix))
        if (
            relative.is_absolute()
            or not relative.parts
            or any(part in {"", ".", ".."} for part in relative.parts)
            or relative.suffix != ".json"
        ):
            return False
        try:
            workspace_resolved = workspace.resolve(strict=True)
            evidence_path = workspace_resolved.joinpath(*relative.parts)
            current_path = workspace_resolved
            for part in relative.parts:
                current_path = current_path / part
                if current_path.is_symlink():
                    return False
            resolved_evidence_path = evidence_path.resolve(strict=True)
            if (
                resolved_evidence_path == workspace_resolved
                or workspace_resolved not in resolved_evidence_path.parents
                or not resolved_evidence_path.is_file()
                or resolved_evidence_path.stat().st_size > 5_000_000
            ):
                return False
            persisted_payload = json.loads(
                resolved_evidence_path.read_text(encoding="utf-8")
            )
        except (
            OSError,
            RuntimeError,
            TypeError,
            UnicodeError,
            ValueError,
            json.JSONDecodeError,
        ):
            return False
        if not isinstance(persisted_payload, dict):
            return False
        carrier = RuntimeError(
            str(safe_failure_evidence.get("reason_code") or "")
        )
        carrier.safe_evidence_kind = safe_failure_evidence.get(
            "safe_evidence_kind"
        )
        carrier.safe_evidence = {
            **persisted_payload,
            "evidence_path": str(resolved_evidence_path),
            "evidence_persisted": True,
        }
        try:
            regenerated = (
                MR1RealProductionService._sanitized_pexels_failure_evidence(
                    exc=carrier,
                    workspace=workspace_resolved,
                )
            )
        except (
            OSError,
            RuntimeError,
            TypeError,
            UnicodeError,
            ValueError,
        ):
            return False
        return regenerated == safe_failure_evidence

    def _provider_continuation_manifest_creator(
        self,
        *,
        state: dict[str, Any],
    ) -> uuid.UUID:
        package_version = self.session.get(
            ArtifactVersion,
            uuid.UUID(state["package_artifact_version_id"]),
        )
        package_artifact = (
            self.session.get(Artifact, package_version.artifact_id)
            if package_version is not None
            else None
        )
        if (
            package_version is None
            or package_artifact is None
            or package_version.content_hash != state["package_content_hash"]
            or content_hash(package_version.content or {})
            != package_version.content_hash
            or package_artifact.video_project_id != uuid.UUID(state["project_id"])
        ):
            raise ValidationFailureError(
                "MR1_PROVIDER_CONTINUATION_PACKAGE_CREATOR_INVALID"
            )
        return package_version.created_by_user_id

    def _persist_provider_continuation_review_manifest(
        self,
        *,
        state: dict[str, Any],
        manifest: dict[str, Any],
        creator_id: uuid.UUID,
    ) -> ArtifactVersion:
        expected_hash = content_hash(manifest)
        existing = self._find_artifact_for_run(
            uuid.UUID(state["run_id"]),
            PROVIDER_CONTINUATION_REVIEW_ARTIFACT_TYPE,
        )
        if existing is not None:
            if (
                existing.content_hash == expected_hash
                and existing.content == manifest
            ):
                return existing
            prior_decision = self.session.scalar(
                select(ApprovalDecision)
                .where(
                    ApprovalDecision.target_artifact_version_id == existing.id,
                    ApprovalDecision.decision == "approved",
                )
                .limit(1)
            )
            if prior_decision is not None:
                raise ValidationFailureError(
                    "MR1_PROVIDER_CONTINUATION_REVIEW_MANIFEST_ALREADY_APPROVED"
                )
            version = self._create_version_on_existing_artifact(
                existing=existing,
                content=manifest,
                actor_id=creator_id,
                correlation_id=(
                    f"mr1-provider-continuation-review-{state['run_id']}"
                ),
            )
        else:
            _, version = self._create_artifact(
                project_id=uuid.UUID(state["project_id"]),
                artifact_type=PROVIDER_CONTINUATION_REVIEW_ARTIFACT_TYPE,
                actor_id=creator_id,
                content=manifest,
                correlation_id=(
                    f"mr1-provider-continuation-review-{state['run_id']}"
                ),
            )
        if (
            version.content_hash != expected_hash
            or content_hash(version.content or {}) != version.content_hash
        ):
            raise ValidationFailureError(
                "MR1_PROVIDER_CONTINUATION_REVIEW_MANIFEST_HASH_INVALID"
            )
        return version

    @staticmethod
    def _provider_continuation_review_resolution_ref(
        review_manifest_version: ArtifactVersion,
    ) -> str:
        return (
            f"artifact-version://{review_manifest_version.id}"
            f"?content_hash={review_manifest_version.content_hash}"
        )

    @staticmethod
    def _provider_continuation_review_evidence_refs(
        *,
        review_manifest_version: ArtifactVersion,
        prior_consumed_snapshot: dict[str, Any],
    ) -> list[dict[str, Any]]:
        return [
            {
                "type": "mr1_provider_attempt_continuation_review_manifest",
                "artifact_version_id": str(review_manifest_version.id),
                "content_hash": review_manifest_version.content_hash,
            },
            {
                "type": "mr1_prior_consumed_pexels_attempt",
                "artifact_version_id": prior_consumed_snapshot[
                    "artifact_version_id"
                ],
                "content_hash": prior_consumed_snapshot["content_hash"],
                "snapshot_content_hash": prior_consumed_snapshot[
                    "snapshot_content_hash"
                ],
            },
        ]

    def _persist_provider_continuation_review_task(
        self,
        *,
        state: dict[str, Any],
        review_manifest_version: ArtifactVersion,
        prior_consumed_snapshot: dict[str, Any],
        operator_id: uuid.UUID,
    ) -> tuple[ReviewTask, list[dict[str, Any]]]:
        superseded_tasks = (
            self._cancel_superseded_provider_continuation_review_tasks(
                state=state,
                review_manifest_version=review_manifest_version,
                operator_id=operator_id,
            )
        )
        matches = list(
            self.session.scalars(
                select(ReviewTask).where(
                    ReviewTask.video_project_id == uuid.UUID(state["project_id"]),
                    ReviewTask.target_artifact_version_id
                    == review_manifest_version.id,
                    ReviewTask.review_type == "final_human",
                )
            ).all()
        )
        if len(matches) > 1:
            raise ValidationFailureError(
                "MR1_PROVIDER_CONTINUATION_REVIEW_TASK_DUPLICATE"
            )
        if matches:
            task = matches[0]
            if not self._provider_continuation_review_task_exact(
                task=task,
                state=state,
                review_manifest_version=review_manifest_version,
                prior_consumed_snapshot=prior_consumed_snapshot,
                expected_operator_id=operator_id,
                expected_approval_decision_id=None,
                require_completed=False,
            ):
                raise ValidationFailureError(
                    "MR1_PROVIDER_CONTINUATION_REVIEW_TASK_STALE_OR_INVALID"
                )
            return task, superseded_tasks
        task = ReviewService(self.session).create_review_task(
            data=ReviewTaskCreate(
                video_project_id=uuid.UUID(state["project_id"]),
                target_type="artifact_version",
                target_id=review_manifest_version.id,
                target_artifact_version_id=review_manifest_version.id,
                review_type="final_human",
                status="open",
                assigned_to_user_id=operator_id,
                requested_by_user_id=operator_id,
                review_reason_codes=deepcopy(
                    PROVIDER_CONTINUATION_REVIEW_REASON_CODES
                ),
                evidence_required=True,
                evidence_refs=self._provider_continuation_review_evidence_refs(
                    review_manifest_version=review_manifest_version,
                    prior_consumed_snapshot=prior_consumed_snapshot,
                ),
                review_scope=PROVIDER_CONTINUATION_REVIEW_SCOPE,
                context_pack_ref=(
                    f"mr1-provider-continuation://{state['run_id']}/"
                    f"{review_manifest_version.id}"
                    f"?content_hash={review_manifest_version.content_hash}"
                ),
            ),
            correlation_id=(
                f"mr1-provider-continuation-review-task-{state['run_id']}"
            ),
        )
        return task, superseded_tasks

    def _cancel_superseded_provider_continuation_review_tasks(
        self,
        *,
        state: dict[str, Any],
        review_manifest_version: ArtifactVersion,
        operator_id: uuid.UUID,
    ) -> list[dict[str, Any]]:
        """Close stale open tasks when a new immutable manifest is proposed."""

        run_id = str(state["run_id"])
        context_prefix = f"mr1-provider-continuation://{run_id}/"
        candidates = list(
            self.session.scalars(
                select(ReviewTask).where(
                    ReviewTask.video_project_id
                    == uuid.UUID(state["project_id"]),
                    ReviewTask.review_type == "final_human",
                )
            ).all()
        )
        superseded: list[dict[str, Any]] = []
        for task in candidates:
            if (
                task.target_artifact_version_id
                == review_manifest_version.id
                or task.target_type != "artifact_version"
                or task.review_reason_codes
                != PROVIDER_CONTINUATION_REVIEW_REASON_CODES
                or not str(task.context_pack_ref or "").startswith(
                    context_prefix
                )
            ):
                continue
            prior_version = (
                self.session.get(
                    ArtifactVersion,
                    task.target_artifact_version_id,
                )
                if task.target_artifact_version_id is not None
                else None
            )
            prior_artifact = (
                self.session.get(Artifact, prior_version.artifact_id)
                if prior_version is not None
                else None
            )
            if (
                prior_version is None
                or prior_artifact is None
                or prior_artifact.video_project_id
                != uuid.UUID(state["project_id"])
                or prior_artifact.artifact_type
                != PROVIDER_CONTINUATION_REVIEW_ARTIFACT_TYPE
                or (prior_version.content or {}).get("run_id") != run_id
                or task.target_id != prior_version.id
                or task.assigned_to_user_id != operator_id
                or task.requested_by_user_id != operator_id
            ):
                raise ValidationFailureError(
                    "MR1_PROVIDER_CONTINUATION_STALE_REVIEW_TASK_INVALID"
                )
            if task.status == "completed":
                raise ValidationFailureError(
                    "MR1_PROVIDER_CONTINUATION_STALE_COMPLETED_TASK_"
                    "REQUIRES_RESOLUTION"
                )
            supersession = next(
                (
                    deepcopy(item)
                    for item in task.evidence_refs or []
                    if item.get("type")
                    == "mr1_provider_continuation_review_superseded"
                ),
                None,
            )
            if task.status in {"open", "in_progress"}:
                supersession = {
                    "type": (
                        "mr1_provider_continuation_review_superseded"
                    ),
                    "reason_code": (
                        "MR1_PROVIDER_CONTINUATION_MANIFEST_CHANGED"
                    ),
                    "superseded_manifest_artifact_version_id": str(
                        prior_version.id
                    ),
                    "superseded_manifest_content_hash": (
                        prior_version.content_hash
                    ),
                    "superseded_by_manifest_artifact_version_id": str(
                        review_manifest_version.id
                    ),
                    "superseded_by_manifest_content_hash": (
                        review_manifest_version.content_hash
                    ),
                }
                task.evidence_refs = [
                    *(task.evidence_refs or []),
                    deepcopy(supersession),
                ]
                task.status = "cancelled"
                self.session.flush()
            elif task.status != "cancelled" or supersession is None:
                raise ValidationFailureError(
                    "MR1_PROVIDER_CONTINUATION_STALE_REVIEW_TASK_"
                    "STATUS_INVALID"
                )
            superseded.append(
                {
                    "review_task_id": str(task.id),
                    "status": task.status,
                    "target_artifact_version_id": str(
                        prior_version.id
                    ),
                    "target_content_hash": prior_version.content_hash,
                    "supersession_evidence": deepcopy(supersession),
                }
            )
        return sorted(
            superseded,
            key=lambda item: item["review_task_id"],
        )

    @classmethod
    def _provider_continuation_review_task_exact(
        cls,
        *,
        task: ReviewTask | None,
        state: dict[str, Any],
        review_manifest_version: ArtifactVersion,
        prior_consumed_snapshot: dict[str, Any],
        expected_operator_id: uuid.UUID,
        expected_approval_decision_id: uuid.UUID | None,
        require_completed: bool,
    ) -> bool:
        if task is None:
            return False
        required_evidence = cls._provider_continuation_review_evidence_refs(
            review_manifest_version=review_manifest_version,
            prior_consumed_snapshot=prior_consumed_snapshot,
        )
        evidence_refs = list(task.evidence_refs or [])
        valid_status = (
            task.status == "completed"
            if require_completed
            else task.status in {"open", "in_progress", "completed"}
        )
        if not (
            task.video_project_id == uuid.UUID(state["project_id"])
            and task.target_type == "artifact_version"
            and task.target_id == review_manifest_version.id
            and task.target_artifact_version_id == review_manifest_version.id
            and task.review_type == "final_human"
            and valid_status
            and task.assigned_to_user_id == expected_operator_id
            and task.requested_by_user_id == expected_operator_id
            and task.review_reason_codes
            == PROVIDER_CONTINUATION_REVIEW_REASON_CODES
            and task.evidence_required is True
            and all(item in evidence_refs for item in required_evidence)
            and task.review_scope == PROVIDER_CONTINUATION_REVIEW_SCOPE
            and task.context_pack_ref
            == (
                f"mr1-provider-continuation://{state['run_id']}/"
                f"{review_manifest_version.id}"
                f"?content_hash={review_manifest_version.content_hash}"
            )
        ):
            return False
        if not require_completed:
            return True
        if expected_approval_decision_id is None:
            return False
        completion_evidence = {
            "type": "explicit_human_approval_resolution",
            "resolution_ref": cls._provider_continuation_review_resolution_ref(
                review_manifest_version
            ),
            "approval_decision_ids": [str(expected_approval_decision_id)],
            "prior_review_reason_codes_retained": deepcopy(
                PROVIDER_CONTINUATION_REVIEW_REASON_CODES
            ),
        }
        return completion_evidence in evidence_refs

    def _validate_exact_unsubmitted_pexels_attempt(
        self,
        *,
        state: dict[str, Any],
        operation_key: str,
        ledger: dict[str, Any],
    ) -> dict[str, Any]:
        scene_id = operation_key.removeprefix("pexels:")
        try:
            artifact = self.session.get(
                Artifact,
                uuid.UUID(state["attempt_artifact_ids"][operation_key]),
            )
            version = (
                self.session.get(ArtifactVersion, artifact.current_version_id)
                if artifact is not None
                and artifact.current_version_id is not None
                else None
            )
        except (KeyError, TypeError, ValueError):
            artifact = None
            version = None
        if artifact is None or version is None:
            raise ValidationFailureError(
                "MR1_PROVIDER_QUERY_AMENDMENT_UNSUBMITTED_LEDGER_MISSING"
            )
        exact_version = self._exact_version(
            version.id,
            version.content_hash,
            ATTEMPT_ARTIFACT_TYPE,
            uuid.UUID(state["project_id"]),
        )
        persisted = deepcopy(exact_version.content or {})
        expected_values = {
            "run_id": state["run_id"],
            "operation_key": operation_key,
            "provider": "pexels_api",
            "operation": "supporting_asset_acquisition",
            "scene_id": scene_id,
            "state": "PLANNED",
            "submit_state": "NOT_SUBMITTED",
            "attempt_cap": 1,
            "attempt_count": 0,
            "network_submit_started": False,
            "search_submit_count": 0,
            "download_submit_count": 0,
            "request_hash": ledger.get("request_hash"),
        }
        if (
            artifact.video_project_id != uuid.UUID(state["project_id"])
            or artifact.current_version_id != exact_version.id
            or ledger.get("artifact_version_id") != str(exact_version.id)
            or any(
                persisted.get(key) != expected
                for key, expected in expected_values.items()
            )
            or any(
                ledger.get(key) != expected
                for key, expected in expected_values.items()
            )
            or list(persisted.get("attempt_outcomes") or [])
            or list(ledger.get("attempt_outcomes") or [])
        ):
            raise ValidationFailureError(
                "MR1_PROVIDER_QUERY_AMENDMENT_UNSUBMITTED_LEDGER_INVALID"
            )
        snapshot = {
            "schema_version": "mr1.unsubmitted-pexels-attempt.v1",
            "artifact_id": str(artifact.id),
            "artifact_version_id": str(exact_version.id),
            "content_hash": exact_version.content_hash,
            **expected_values,
        }
        snapshot["snapshot_content_hash"] = content_hash(snapshot)
        return snapshot

    def _validate_exact_prior_consumed_pexels_attempt(
        self,
        *,
        state: dict[str, Any],
        operation_key: str,
        ledger: dict[str, Any],
        version_id: uuid.UUID,
        expected_content_hash: str,
    ) -> dict[str, Any]:
        scene_id = operation_key.removeprefix("pexels:")
        version = self._exact_version(
            version_id,
            expected_content_hash,
            ATTEMPT_ARTIFACT_TYPE,
            uuid.UUID(state["project_id"]),
            fresh_lock=True,
        )
        artifact = self.session.get(Artifact, version.artifact_id)
        persisted = deepcopy(version.content or {})
        selected_fields = (
            "run_id",
            "operation_key",
            "provider",
            "operation",
            "scene_id",
            "state",
            "submit_state",
            "attempt_cap",
            "attempt_count",
            "network_submit_started",
            "search_submit_count",
            "download_submit_count",
            "request_hash",
            "failure",
        )
        expected_values = {
            "run_id": state["run_id"],
            "operation_key": operation_key,
            "provider": "pexels_api",
            "operation": "supporting_asset_acquisition",
            "scene_id": scene_id,
            "state": "CONSUMED_FAILED",
            "submit_state": "FAILED_CONSUMED",
            "attempt_cap": 1,
            "attempt_count": 1,
            "network_submit_started": True,
            "search_submit_count": 1,
            "download_submit_count": 0,
            "request_hash": ledger.get("request_hash"),
            "failure": "RuntimeError:PEXELS_SEMANTIC_FIT_INADEQUATE",
        }
        persisted_matches = all(
            persisted.get(key) == expected_values[key] for key in selected_fields
        )
        state_matches = all(
            ledger.get(key) == expected_values[key] for key in selected_fields
        )
        outcomes = persisted.get("attempt_outcomes") or []
        last_outcome = outcomes[-1] if outcomes else {}
        persisted_safe_failure = persisted.get("safe_failure_evidence")
        state_safe_failure = ledger.get("safe_failure_evidence")
        safe_failure_exact = (
            persisted_safe_failure is None
            and state_safe_failure is None
        )
        if (
            isinstance(persisted_safe_failure, dict)
            and persisted_safe_failure == state_safe_failure
        ):
            safe_failure_without_hash = {
                key: deepcopy(value)
                for key, value in persisted_safe_failure.items()
                if key != "content_hash"
            }
            safe_failure_exact = (
                persisted_safe_failure.get("schema_version")
                == "mr1.pexels-safe-failure-evidence-ref.v1"
                and persisted_safe_failure.get("safe_evidence_kind")
                == "PEXELS_SEARCH_RANKING_FAILURE"
                and persisted_safe_failure.get("guarded_key")
                == "pexels_search_ranking_failure"
                and persisted_safe_failure.get("reason_code")
                == "PEXELS_SEMANTIC_FIT_INADEQUATE"
                and persisted_safe_failure.get("content_hash")
                == content_hash(safe_failure_without_hash)
                and (last_outcome.get("safe_failure_evidence_ref") or {}).get(
                    "content_hash"
                )
                == persisted_safe_failure.get("content_hash")
                and self._sanitized_pexels_failure_evidence_ref_exact(
                    safe_failure_evidence=persisted_safe_failure,
                    workspace=Path(state["workspace"]),
                )
            )
        if (
            artifact is None
            or str(artifact.id)
            != state["attempt_artifact_ids"].get(operation_key)
            or ledger.get("artifact_version_id") != str(version.id)
            or not persisted_matches
            or not state_matches
            or last_outcome.get("state") != "CONSUMED_FAILED"
            or last_outcome.get("request_hash") != expected_values["request_hash"]
            or last_outcome.get("failure") != expected_values["failure"]
            or not safe_failure_exact
        ):
            raise ValidationFailureError(
                "MR1_PROVIDER_CONTINUATION_PRIOR_LEDGER_CONTENT_INVALID"
            )
        snapshot = {
            "schema_version": "mr1.prior-consumed-pexels-attempt.v1",
            "artifact_id": str(artifact.id),
            "artifact_version_id": str(version.id),
            "content_hash": version.content_hash,
            **expected_values,
        }
        if isinstance(persisted_safe_failure, dict):
            snapshot["safe_failure_evidence"] = deepcopy(
                persisted_safe_failure
            )
        snapshot["snapshot_content_hash"] = content_hash(snapshot)
        return snapshot

    @staticmethod
    def _provider_continuation_decision_exact(
        *,
        decision: ApprovalDecision | None,
        state: dict[str, Any],
        authorization_scope: dict[str, Any],
        authorization_hash: str,
        review_manifest_version: ArtifactVersion,
        expected_operator_id: uuid.UUID,
        expected_review_task_id: uuid.UUID,
    ) -> bool:
        if decision is None:
            return False
        metadata = decision.metadata_ or {}
        return bool(
            decision.decision == "approved"
            and decision.target_type == "artifact_version"
            and decision.target_id == review_manifest_version.id
            and decision.target_artifact_version_id == review_manifest_version.id
            and decision.decided_by_user_id == expected_operator_id
            and decision.decision_basis == authorization_scope
            and metadata.get("approval_scope")
            == PROVIDER_ATTEMPT_CONTINUATION_SCOPE
            and metadata.get("authorization_content_hash")
            == authorization_hash
            and metadata.get("operator_review_manifest_artifact_version_id")
            == str(review_manifest_version.id)
            and metadata.get("operator_review_manifest_content_hash")
            == review_manifest_version.content_hash
            and metadata.get("operator_review_task_id")
            == str(expected_review_task_id)
            and authorization_scope.get("operator_review_task_id")
            == str(expected_review_task_id)
            and metadata.get("run_id") == state["run_id"]
            and metadata.get("operation_key")
            == authorization_scope["operation_key"]
            and metadata.get("additional_attempts") == 1
            and metadata.get("maximum_total_attempts") == 2
            and metadata.get("automatic_retry_allowed") is False
            and metadata.get("provider_substitution_allowed") is False
            and metadata.get("publish_execution_authorized") is False
        )

    @staticmethod
    def _provider_continuation_review_manifest_exact(
        *,
        manifest: dict[str, Any],
        continuation: dict[str, Any],
    ) -> bool:
        exact_fields = (
            "run_id",
            "project_id",
            "operation_key",
            "scene_id",
            "provider",
            "route",
            "base_approval_id",
            "base_approval_content_hash",
            "package_artifact_version_id",
            "package_content_hash",
            "prior_consumed_attempt",
            "prior_request_schema",
            "prior_request_hash",
            "package_semantic_intent",
            "approved_stock_search_intent",
            "approved_query_authority",
            "base_query_evidence",
            "query_material_diff",
            "stock_search_intent_derivation",
            "query_intent_coverage_evidence",
            "approved_pending_scene_stock_search_intents",
            "pending_query_amendments",
            "additional_attempts",
            "maximum_total_attempts",
            "semantic_fit_threshold",
            "canonical_timeline_hash",
            "automatic_retry_allowed",
            "provider_substitution_allowed",
            "automatic_pexels_to_ai_fallback",
            "incremental_cost_cap_usd",
            "youtube_upload_authorized",
            "publish_execution_authorized",
        )
        return bool(
            manifest.get("schema_version")
            == "mr1.provider-attempt-continuation-review-manifest.v1"
            and manifest.get("provider_calls_made_by_review_preparation") == 0
            and all(
                manifest.get(field) == continuation.get(field)
                for field in exact_fields
            )
        )

    def _save_attempt(
        self, state: dict[str, Any], operation_key: str, actor_id: uuid.UUID
    ) -> ArtifactVersion:
        artifact_id = uuid.UUID(state["attempt_artifact_ids"][operation_key])
        artifact = self.session.get(Artifact, artifact_id)
        if artifact is None or artifact.current_version_id is None:
            raise ValidationFailureError("MR1_ATTEMPT_LEDGER_MISSING")
        parent = self.session.get(ArtifactVersion, artifact.current_version_id)
        evidence_refs = [
            {"type": "mr1_execution_run", "run_id": state["run_id"]},
            {
                "type": "mr1_approval",
                "approval_id": state["approval_id"],
                "content_hash": state["approval_content_hash"],
            },
        ]
        continuation = state["attempts"][operation_key].get(
            "provider_attempt_continuation"
        )
        if isinstance(continuation, dict):
            evidence_refs.append(
                {
                    "type": "mr1_provider_attempt_continuation_approval",
                    "approval_id": continuation["approval_decision_id"],
                    "content_hash": continuation["authorization_content_hash"],
                    "review_manifest_artifact_version_id": continuation[
                        "operator_review_manifest_artifact_version_id"
                    ],
                    "review_manifest_content_hash": continuation[
                        "operator_review_manifest_content_hash"
                    ],
                    "operator_review_task_id": continuation[
                        "operator_review_task_id"
                    ],
                }
            )
        query_amendment = state["attempts"][operation_key].get(
            "provider_query_amendment"
        )
        if isinstance(query_amendment, dict):
            evidence_refs.append(
                {
                    "type": "mr1_provider_query_amendment_approval",
                    "approval_id": query_amendment["approval_decision_id"],
                    "content_hash": query_amendment[
                        "authorization_content_hash"
                    ],
                    "review_manifest_artifact_version_id": (
                        query_amendment[
                            "operator_review_manifest_artifact_version_id"
                        ]
                    ),
                    "review_manifest_content_hash": query_amendment[
                        "operator_review_manifest_content_hash"
                    ],
                    "operator_review_task_id": query_amendment[
                        "operator_review_task_id"
                    ],
                }
            )
        version = ArtifactService(self.session).create_artifact_version(
            data=ArtifactVersionCreate(
                artifact_id=artifact.id,
                parent_version_id=parent.id,
                content=deepcopy(state["attempts"][operation_key]),
                status="approved",
                created_by_user_id=actor_id,
                evidence_refs=evidence_refs,
            ),
            correlation_id=f"mr1-attempt-update-{operation_key}",
        )
        state["attempts"][operation_key]["artifact_version_id"] = str(version.id)
        return version

    def _save_run(
        self, artifact: Artifact, state: dict[str, Any], *, actor_id: uuid.UUID
    ) -> ArtifactVersion:
        current = self.session.get(ArtifactVersion, artifact.current_version_id)
        if current is None:
            raise ValidationFailureError("MR1_RUN_CURRENT_VERSION_MISSING")
        payload = deepcopy(state)
        payload.pop("run_artifact_version_id", None)
        version = ArtifactService(self.session).create_artifact_version(
            data=ArtifactVersionCreate(
                artifact_id=artifact.id,
                parent_version_id=current.id,
                content=payload,
                status="approved",
                created_by_user_id=actor_id,
                evidence_refs=[
                    {
                        "type": "mr1_approval",
                        "approval_id": state["approval_id"],
                        "content_hash": state["approval_content_hash"],
                    }
                ],
                packaging_metadata={
                    "production_eligible": True,
                    "not_publishable": True,
                    "youtube_upload": "PROHIBITED",
                },
            ),
            correlation_id=f"mr1-run-update-{state['run_id']}",
        )
        state["run_artifact_version_id"] = str(version.id)
        if state.get("workspace"):
            _write_json_atomic(Path(state["workspace"]) / "run_state.json", state)
        return version

    def _create_artifact(
        self,
        *,
        project_id: uuid.UUID,
        artifact_type: str,
        actor_id: uuid.UUID,
        content: dict[str, Any],
        correlation_id: str,
    ) -> tuple[Artifact, ArtifactVersion]:
        service = ArtifactService(self.session)
        artifact = service.create_artifact(
            data=ArtifactCreate(
                video_project_id=project_id,
                artifact_type=artifact_type,
                status="approved",
                created_by_user_id=actor_id,
            ),
            correlation_id=correlation_id,
        )
        version = service.create_artifact_version(
            data=ArtifactVersionCreate(
                artifact_id=artifact.id,
                content=deepcopy(content),
                status="approved",
                created_by_user_id=actor_id,
            ),
            correlation_id=f"{correlation_id}-version",
        )
        return artifact, version

    def _persist_candidate_once(
        self,
        *,
        state: dict[str, Any],
        candidate_content: dict[str, Any],
        actor_id: uuid.UUID,
    ) -> ArtifactVersion:
        existing = self._find_artifact_for_run(
            uuid.UUID(state["run_id"]), CANDIDATE_ARTIFACT_TYPE
        )
        if existing is not None:
            if existing.content == candidate_content:
                return existing
            if int(state.get("review_round") or 1) <= 1:
                raise ValidationFailureError("MR1_CANDIDATE_IDEMPOTENCY_CONFLICT")
            return self._create_version_on_existing_artifact(
                existing=existing,
                content=candidate_content,
                actor_id=actor_id,
                correlation_id=(
                    f"mr1-review-candidate-{state['run_id']}-"
                    f"round-{int(state['review_round'])}"
                ),
            )
        _, version = self._create_artifact(
            project_id=uuid.UUID(state["project_id"]),
            artifact_type=CANDIDATE_ARTIFACT_TYPE,
            actor_id=actor_id,
            content=candidate_content,
            correlation_id=f"mr1-review-candidate-{state['run_id']}",
        )
        return version

    def _persist_technical_qc_once(
        self,
        *,
        state: dict[str, Any],
        candidate_content: dict[str, Any],
        candidate_version: ArtifactVersion,
        actor_id: uuid.UUID,
    ) -> ArtifactVersion:
        qc_path = Path(str(candidate_content.get("technical_media_qc_ref") or ""))
        try:
            qc_payload = json.loads(qc_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValidationFailureError("MR1_TECHNICAL_QC_SOURCE_INVALID") from exc
        if not isinstance(qc_payload, dict):
            raise ValidationFailureError("MR1_TECHNICAL_QC_SOURCE_INVALID")
        supplied_hash = qc_payload.get("content_hash")
        qc_core = {
            key: value for key, value in qc_payload.items() if key != "content_hash"
        }
        review_round = int(state.get("review_round") or 1)
        if (
            qc_path.is_symlink()
            or supplied_hash != candidate_content.get("technical_media_qc_hash")
            or supplied_hash != content_hash(qc_core)
            or qc_payload.get("run_id") != state["run_id"]
            or qc_payload.get("result") != "PASS"
            or qc_payload.get("output_sha256") != candidate_content.get("output_sha256")
            or qc_payload.get("production_eligible") is not True
            or qc_payload.get("not_publishable") is not True
            or qc_payload.get("human_full_watch_still_required") is not True
        ):
            raise ValidationFailureError("MR1_TECHNICAL_QC_AUTHORITY_INVALID")
        payload = {
            "schema_version": "mr1.technical-media-qc-receipt.v1",
            "run_id": state["run_id"],
            "project_id": state["project_id"],
            "review_round": review_round,
            "review_media_candidate": {
                "artifact_version_id": str(candidate_version.id),
                "content_hash": candidate_version.content_hash,
            },
            "output_sha256": candidate_content["output_sha256"],
            "source_qc_file": {
                "file_ref": str(qc_path.resolve(strict=True)),
                "file_sha256": _sha256_file(qc_path),
                "content_hash": supplied_hash,
            },
            "technical_qc": deepcopy(qc_payload),
            "result": "PASS",
            "production_eligible": True,
            "not_publishable": True,
            "human_full_watch_still_required": True,
        }
        existing = self._find_artifact_for_run(
            uuid.UUID(state["run_id"]), TECHNICAL_QC_ARTIFACT_TYPE
        )
        if existing is not None:
            if existing.content == payload:
                return existing
            if review_round <= 1:
                raise ValidationFailureError("MR1_TECHNICAL_QC_IDEMPOTENCY_CONFLICT")
            return self._create_version_on_existing_artifact(
                existing=existing,
                content=payload,
                actor_id=actor_id,
                correlation_id=(
                    f"mr1-technical-qc-{state['run_id']}-round-{review_round}"
                ),
            )
        _, version = self._create_artifact(
            project_id=uuid.UUID(state["project_id"]),
            artifact_type=TECHNICAL_QC_ARTIFACT_TYPE,
            actor_id=actor_id,
            content=payload,
            correlation_id=f"mr1-technical-qc-{state['run_id']}-round-1",
        )
        return version

    def _persist_drive_receipt_once(
        self,
        *,
        state: dict[str, Any],
        receipt: dict[str, Any],
        candidate_version: ArtifactVersion,
        actor_id: uuid.UUID,
    ) -> ArtifactVersion:
        final_manifest = state.get("final_drive_request_manifest") or {}
        expected_count = int(final_manifest.get("item_count") or 0)
        verification = deepcopy(receipt.get("verification") or {})
        required_proofs = {
            "exact_item_set",
            "exact_item_count",
            "correct_parent",
            "correct_names",
            "size_verified",
            "checksum_readback_verified",
            "duplicate_absence",
            "receipt_hash_valid",
            "final_request_manifest_exact",
            "archive_identity_exact",
            "run_identity_exact",
            "provider_archive_state_verified",
        }
        if (
            expected_count <= 0
            or set(verification) != required_proofs
            or not all(value is True for value in verification.values())
            or receipt.get("expected_item_count") != expected_count
            or receipt.get("exact_item_count") != expected_count
            or receipt.get("verified_item_count") != expected_count
            or receipt.get("actual_item_count") != expected_count
            or receipt.get("remote_item_count") != expected_count
            or receipt.get("duplicate_count") != 0
            or receipt.get("final_drive_request_manifest_hash")
            != final_manifest.get("content_hash")
            or receipt.get("item_set_hash") != final_manifest.get("item_set_hash")
        ):
            raise ValidationFailureError("MR1_DRIVE_RECEIPT_STRICT_PROOF_REQUIRED")
        payload = {
            **deepcopy(receipt),
            "run_id": state["run_id"],
            "archive_identity": state["archive_identity"],
            "status": "VERIFIED",
            "ARCHIVE_VERIFIED": True,
            "review_media_candidate_artifact_version_id": str(candidate_version.id),
            "review_media_candidate_content_hash": candidate_version.content_hash,
            "output_sha256": state["review_media_candidate"]["output_sha256"],
            "expected_item_count": expected_count,
            "actual_item_count": expected_count,
            "remote_exact_set_verified": True,
            "verification": verification,
        }
        existing = self._find_artifact_for_run(
            uuid.UUID(state["run_id"]), DRIVE_RECEIPT_ARTIFACT_TYPE
        )
        if existing is not None:
            if existing.content == payload:
                return existing
            if int(state.get("review_round") or 1) <= 1:
                raise ValidationFailureError("MR1_DRIVE_RECEIPT_IDEMPOTENCY_CONFLICT")
            return self._create_version_on_existing_artifact(
                existing=existing,
                content=payload,
                actor_id=actor_id,
                correlation_id=(
                    f"mr1-drive-receipt-{state['run_id']}-"
                    f"round-{int(state['review_round'])}"
                ),
            )
        _, version = self._create_artifact(
            project_id=uuid.UUID(state["project_id"]),
            artifact_type=DRIVE_RECEIPT_ARTIFACT_TYPE,
            actor_id=actor_id,
            content=payload,
            correlation_id=f"mr1-drive-receipt-{state['run_id']}",
        )
        return version

    def _ensure_final_human_review_task(
        self,
        *,
        state: dict[str, Any],
        candidate_version: ArtifactVersion,
        requested_by_user_id: uuid.UUID,
    ) -> ReviewTask:
        review_round = int(state.get("review_round") or 1)
        drive = state.get("drive_archive") or {}
        reason_codes = self._final_human_review_reason_codes(review_round)
        context_pack_ref = self._final_human_review_context_ref(state, review_round)
        evidence_refs = [
            {
                "type": "mr1_review_media_candidate",
                "artifact_version_id": str(candidate_version.id),
                "content_hash": candidate_version.content_hash,
                "review_round": review_round,
            },
            {
                "type": "mr1_drive_archive_receipt",
                "artifact_version_id": drive.get("artifact_version_id"),
                "content_hash": drive.get("content_hash"),
                "review_round": review_round,
            },
            {
                "type": "mr1_exact_package",
                "artifact_version_id": state["package_artifact_version_id"],
                "content_hash": state["package_content_hash"],
            },
            {
                "type": "mr1_technical_media_qc_receipt",
                "artifact_version_id": (state.get("technical_media_qc") or {}).get(
                    "artifact_version_id"
                ),
                "content_hash": (state.get("technical_media_qc") or {}).get(
                    "content_hash"
                ),
                "review_round": review_round,
            },
        ]
        matches = list(
            self.session.scalars(
                select(ReviewTask).where(
                    ReviewTask.video_project_id == uuid.UUID(state["project_id"]),
                    ReviewTask.target_artifact_version_id == candidate_version.id,
                    ReviewTask.review_type == "final_human",
                )
            ).all()
        )
        if len(matches) > 1:
            raise ValidationFailureError("MR1_FINAL_HUMAN_REVIEW_TASK_DUPLICATE")
        if matches:
            task = matches[0]
            if (
                task.target_id != candidate_version.id
                or task.assigned_to_user_id != requested_by_user_id
                or task.requested_by_user_id != requested_by_user_id
                or task.status not in {"open", "in_progress"}
                or task.evidence_required is not True
                or task.evidence_refs != evidence_refs
                or task.review_reason_codes != reason_codes
                or task.context_pack_ref != context_pack_ref
            ):
                raise ValidationFailureError(
                    "MR1_FINAL_HUMAN_REVIEW_TASK_STALE_OR_INVALID"
                )
            return task
        return ReviewService(self.session).create_review_task(
            data=ReviewTaskCreate(
                video_project_id=uuid.UUID(state["project_id"]),
                target_type="artifact_version",
                target_id=candidate_version.id,
                target_artifact_version_id=candidate_version.id,
                review_type="final_human",
                status="open",
                assigned_to_user_id=requested_by_user_id,
                requested_by_user_id=requested_by_user_id,
                review_reason_codes=reason_codes,
                evidence_required=True,
                evidence_refs=evidence_refs,
                review_scope=(
                    "Watch the exact current MR1 review candidate in full and "
                    "decide PASS or REJECT. This task cannot authorize a prior "
                    "candidate, prior Drive archive, upload, or publication."
                ),
                context_pack_ref=context_pack_ref,
            ),
            correlation_id=(f"mr1-final-human-{state['run_id']}-round-{review_round}"),
        )

    @staticmethod
    def _final_human_review_reason_codes(review_round: int) -> list[str]:
        return [
            "MR1_EXACT_FINAL_MEDIA_FULL_WATCH_REQUIRED",
            f"MR1_REVIEW_ROUND_{review_round}",
        ]

    @staticmethod
    def _final_human_review_context_ref(
        state: dict[str, Any], review_round: int
    ) -> str:
        return f"mr1-final-human://{state['run_id']}/review-round-{review_round}"

    @staticmethod
    def _human_review_resolution_ref(receipt: ArtifactVersion) -> str:
        return f"artifact-version://{receipt.id}?content_hash={receipt.content_hash}"

    def _persist_human_receipt(
        self,
        *,
        state: dict[str, Any],
        content: dict[str, Any],
        actor_id: uuid.UUID,
    ) -> ArtifactVersion:
        existing = self._find_human_receipt(uuid.UUID(state["run_id"]))
        if existing is not None:
            if existing.content == content:
                return existing
            if self._human_receipt_matches_candidate_content(existing, content):
                raise ValidationFailureError("MR1_HUMAN_RECEIPT_IDEMPOTENCY_CONFLICT")
            return self._create_version_on_existing_artifact(
                existing=existing,
                content=content,
                actor_id=actor_id,
                correlation_id=(
                    f"mr1-human-review-{state['run_id']}-"
                    f"round-{int(content['review_round'])}"
                ),
            )
        _, version = self._create_artifact(
            project_id=uuid.UUID(state["project_id"]),
            artifact_type=HUMAN_RECEIPT_ARTIFACT_TYPE,
            actor_id=actor_id,
            content=content,
            correlation_id=f"mr1-human-review-{state['run_id']}-round-1",
        )
        return version

    def _create_version_on_existing_artifact(
        self,
        *,
        existing: ArtifactVersion,
        content: dict[str, Any],
        actor_id: uuid.UUID,
        correlation_id: str,
    ) -> ArtifactVersion:
        return ArtifactService(self.session).create_artifact_version(
            data=ArtifactVersionCreate(
                artifact_id=existing.artifact_id,
                content=deepcopy(content),
                status="approved",
                created_by_user_id=actor_id,
                parent_version_id=existing.id,
            ),
            correlation_id=correlation_id,
        )

    @staticmethod
    def _human_receipt_matches_candidate(
        receipt: ArtifactVersion | None,
        candidate: ArtifactVersion,
    ) -> bool:
        if receipt is None:
            return False
        binding = (receipt.content or {}).get("review_media_candidate") or {}
        return bool(
            binding.get("artifact_version_id") == str(candidate.id)
            and binding.get("content_hash") == candidate.content_hash
        )

    @staticmethod
    def _human_receipt_matches_candidate_content(
        receipt: ArtifactVersion,
        content: dict[str, Any],
    ) -> bool:
        prior = (receipt.content or {}).get("review_media_candidate") or {}
        current = content.get("review_media_candidate") or {}
        return prior == current

    @staticmethod
    def _human_receipt_matches_command(
        receipt: ArtifactVersion | None,
        command: MR1FinalMediaCloseoutCommand,
    ) -> bool:
        if receipt is None:
            return False
        body = receipt.content or {}
        candidate = body.get("review_media_candidate") or {}
        drive = body.get("drive_archive_receipt") or {}
        return bool(
            body.get("decision") == command.decision
            and body.get("project_id") == str(command.project_id)
            and body.get("decision_source") == command.decision_source
            and body.get("review_authority") == command.review_authority
            and body.get("operator_decision_text") == command.operator_decision_text
            and body.get("decided_by_user_id") == str(command.decided_by_user_id)
            and body.get("reviewed_output_sha256") == command.reviewed_output_sha256
            and body.get("archive_identity") == command.archive_identity
            and candidate.get("artifact_version_id")
            == str(command.review_media_candidate_artifact_version_id)
            and candidate.get("content_hash")
            == command.review_media_candidate_content_hash
            and drive.get("artifact_version_id")
            == str(command.drive_archive_receipt_artifact_version_id)
            and drive.get("content_hash") == command.drive_archive_receipt_content_hash
            and drive.get("archive_identity") == command.archive_identity
        )

    def _require_technical_qc_authority(
        self,
        *,
        state: dict[str, Any],
        candidate: ArtifactVersion,
        candidate_content: dict[str, Any],
    ) -> ArtifactVersion:
        ref = state.get("technical_media_qc") or {}
        try:
            version = self._exact_version(
                uuid.UUID(str(ref["artifact_version_id"])),
                str(ref["content_hash"]),
                TECHNICAL_QC_ARTIFACT_TYPE,
                uuid.UUID(state["project_id"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValidationFailureError("MR1_TECHNICAL_QC_REF_INVALID") from exc
        payload = version.content or {}
        candidate_ref = payload.get("review_media_candidate") or {}
        source = payload.get("source_qc_file") or {}
        source_path = Path(str(source.get("file_ref") or ""))
        technical = payload.get("technical_qc") or {}
        supplied_technical_hash = technical.get("content_hash")
        technical_core = {
            key: value for key, value in technical.items() if key != "content_hash"
        }
        review_round = int(state.get("review_round") or 1)
        if (
            payload.get("run_id") != state["run_id"]
            or payload.get("project_id") != state["project_id"]
            or payload.get("review_round") != review_round
            or ref.get("review_round") != review_round
            or ref.get("output_sha256") != candidate_content.get("output_sha256")
            or candidate_ref.get("artifact_version_id") != str(candidate.id)
            or candidate_ref.get("content_hash") != candidate.content_hash
            or payload.get("output_sha256") != candidate_content.get("output_sha256")
            or payload.get("result") != "PASS"
            or payload.get("production_eligible") is not True
            or payload.get("not_publishable") is not True
            or payload.get("human_full_watch_still_required") is not True
            or not source_path.is_file()
            or source_path.is_symlink()
            or source.get("file_sha256") != _sha256_file(source_path)
            or source.get("content_hash")
            != candidate_content.get("technical_media_qc_hash")
            or supplied_technical_hash != source.get("content_hash")
            or supplied_technical_hash != content_hash(technical_core)
            or technical.get("run_id") != state["run_id"]
            or technical.get("result") != "PASS"
            or technical.get("output_sha256") != candidate_content.get("output_sha256")
        ):
            raise ValidationFailureError("MR1_TECHNICAL_QC_AUTHORITY_INVALID")
        try:
            disk_payload = json.loads(source_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValidationFailureError("MR1_TECHNICAL_QC_SOURCE_INVALID") from exc
        if disk_payload != technical:
            raise ValidationFailureError("MR1_TECHNICAL_QC_SOURCE_CONTENT_MISMATCH")
        return version

    def _validate_candidate_payload(
        self, state: dict[str, Any], candidate: dict[str, Any]
    ) -> None:
        self._revalidate_candidate_authority_bindings(
            state=state,
            candidate=candidate,
        )
        path = Path(str(candidate.get("output_file_ref") or ""))
        sha = candidate.get("output_sha256")
        embedded_hash = candidate.get("content_hash")
        candidate_core = {
            key: deepcopy(value)
            for key, value in candidate.items()
            if key != "content_hash"
        }

        def evidence_json_exact(path_key: str, hash_key: str) -> bool:
            raw = candidate.get(path_key)
            expected = candidate.get(hash_key)
            if not isinstance(raw, str) or not isinstance(expected, str):
                return False
            evidence_path = Path(raw)
            try:
                payload = json.loads(evidence_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return False
            if not isinstance(payload, dict):
                return False
            supplied = payload.get("content_hash")
            core = {
                key: value for key, value in payload.items() if key != "content_hash"
            }
            return bool(supplied == expected and supplied == content_hash(core))

        technical_evidence_exact = evidence_json_exact(
            "technical_media_qc_ref",
            "technical_media_qc_hash",
        )
        creative_evidence_exact = evidence_json_exact(
            "creative_media_qc_ref",
            "creative_media_qc_hash",
        )
        if candidate.get("technical_qc_result") != "PASS":
            raise ValidationFailureError("MR1_TECHNICAL_QC_PASS_REQUIRED")
        if candidate.get("creative_review_result") not in {
            "PASS",
            "REVIEW_REQUIRED",
            "ACCEPTED",
        }:
            raise ValidationFailureError(
                "MR1_CREATIVE_MEDIA_QC_ACCEPTANCE_REQUIRED"
            )
        if (
            not path.is_file()
            or path.is_symlink()
            or not _is_sha256(str(sha or ""))
            or _sha256_file(path) != sha
        ):
            raise ValidationFailureError(
                "MR1_REVIEW_MEDIA_OUTPUT_SHA256_MISMATCH"
            )
        required = (
            candidate.get("run_id") == state["run_id"],
            candidate.get("project_id") == state["project_id"],
            candidate.get("package_artifact_version_id")
            == state["package_artifact_version_id"],
            candidate.get("package_content_hash") == state["package_content_hash"],
            candidate.get("approval_id") == state["approval_id"],
            candidate.get("approval_content_hash") == state["approval_content_hash"],
            candidate.get("canonical_timeline_hash")
            == (state.get("temporal_authority") or {}).get("timeline_hash"),
            candidate.get("review_round") == int(state.get("review_round") or 1),
            candidate.get("production_eligible") is True,
            candidate.get("not_publishable") is True,
            candidate.get("human_review_status") == "PENDING",
            candidate.get("package_lineage_valid") is True,
            candidate.get("legacy_incomplete_package") is False,
            candidate.get("provenance_complete") is True,
            candidate.get("rights_disclosure_resolved") is True,
            candidate.get("lineage_derivation_checks")
            == {
                "package_version_exact": True,
                "approval_exact": True,
                "profile_snapshot_exact": True,
                "rights_planning_authority_exact": True,
                "synthetic_disclosure_authority_exact": True,
                "provenance_plan_authority_exact": True,
                "actual_provenance_manifest_exact": True,
            },
            isinstance(embedded_hash, str) and len(embedded_hash) == 64,
            embedded_hash == content_hash(candidate_core),
            creative_evidence_exact,
        )
        if not all(required):
            raise ValidationFailureError("MR1_REVIEW_MEDIA_CANDIDATE_INVALID")
        if not technical_evidence_exact:
            raise ValidationFailureError("MR1_TECHNICAL_QC_AUTHORITY_INVALID")

    def _revalidate_candidate_authority_bindings(
        self,
        *,
        state: dict[str, Any],
        candidate: dict[str, Any],
    ) -> None:
        """Reopen every authority behind candidate lineage PASS claims."""

        self._revalidate_final_media_lineage_authority(state)
        frozen = deepcopy(state.get("candidate_authority_bindings") or {})
        supplied_hash = frozen.pop("content_hash", None)
        candidate_frozen = candidate.get("candidate_authority_bindings")
        if (
            frozen.get("schema_version") != "mr1.candidate-authority-bindings.v1"
            or not isinstance(supplied_hash, str)
            or supplied_hash != content_hash(frozen)
            or candidate.get("candidate_authority_bindings_hash") != supplied_hash
        ):
            raise ValidationFailureError("MR1_CANDIDATE_FROZEN_AUTHORITY_INVALID")
        frozen["content_hash"] = supplied_hash
        if candidate_frozen != frozen:
            raise ValidationFailureError("MR1_CANDIDATE_FROZEN_AUTHORITY_CHANGED")

        package_ref = frozen.get("package") or {}
        approval_ref = frozen.get("approval") or {}
        exact_bindings = state.get("exact_bindings") or {}
        if (
            package_ref
            != {
                "artifact_version_id": state.get("package_artifact_version_id"),
                "content_hash": state.get("package_content_hash"),
            }
            or approval_ref
            != {
                "approval_decision_id": state.get("approval_id"),
                "approval_content_hash": state.get("approval_content_hash"),
            }
            or frozen.get("channel_profile_version")
            != exact_bindings.get("channel_profile_version")
            or frozen.get("compiled_channel_policy_snapshot")
            != exact_bindings.get("compiled_channel_policy_snapshot")
        ):
            raise ValidationFailureError(
                "MR1_CANDIDATE_PRIMARY_AUTHORITY_BINDING_INVALID"
            )

        artifact_types = {
            "rights_disclosure_completeness_report": (
                "rights_disclosure_completeness_report"
            ),
            "synthetic_media_disclosure_receipt_draft": (
                "synthetic_media_disclosure_receipt_draft"
            ),
            "asset_provenance_plan": "asset_provenance_plan",
            "visual_plan": "visual_plan",
            "provider_execution_plan": "provider_execution_plan",
            "supplemental_visual_alignment": "market_gate_results",
        }
        project_id = uuid.UUID(str(state["project_id"]))
        reopened: dict[str, dict[str, Any]] = {}
        for key, artifact_type in artifact_types.items():
            if key not in frozen:
                if key == "supplemental_visual_alignment":
                    continue
                raise ValidationFailureError(f"MR1_CANDIDATE_AUTHORITY_MISSING:{key}")
            ref = frozen[key]
            try:
                version = self._exact_version(
                    uuid.UUID(str(ref["artifact_version_id"])),
                    str(ref["content_hash"]),
                    artifact_type,
                    project_id,
                    allow_cross_project=True,
                    status_policy="package_authority",
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise ValidationFailureError(
                    f"MR1_CANDIDATE_AUTHORITY_REF_INVALID:{key}"
                ) from exc
            if (
                str(version.artifact_id) != ref.get("artifact_id")
                or ref.get("artifact_version_ref") != f"artifact-version://{version.id}"
                or version.version_number != ref.get("version_number")
            ):
                raise ValidationFailureError(
                    f"MR1_CANDIDATE_AUTHORITY_BINDING_INVALID:{key}"
                )
            reopened[key] = deepcopy(version.content or {})

        rights = reopened["rights_disclosure_completeness_report"]
        disclosure = reopened["synthetic_media_disclosure_receipt_draft"]
        provenance_plan = reopened["asset_provenance_plan"]
        if (
            rights.get("planning_state") != "PASS"
            or rights.get("decision") != "PASS"
            or rights.get("provider_outputs_claimed") is not False
            or rights.get("generated_evidence_authority") is not False
            or rights.get("archive_before_purge") is not True
            or disclosure.get("receipt_status") != "PRE_RENDER_PLANNED"
            or disclosure.get("provider_outputs_exist") is not False
            or disclosure.get("synthetic_voice_planned") is not True
            or disclosure.get("synthetic_image_planned") is not False
            or disclosure.get("synthetic_video_planned") is not False
            or provenance_plan.get("provider_output_exists") is not False
            or provenance_plan.get("generated_evidence_authority") is not False
        ):
            raise ValidationFailureError(
                "MR1_CANDIDATE_RIGHTS_PROVENANCE_AUTHORITY_INVALID"
            )

        raw_path = candidate.get("asset_provenance_manifest_ref")
        expected_path = (
            Path(state["workspace"]) / "assets" / "asset-provenance-manifest.json"
        )
        try:
            provenance_path = Path(str(raw_path or ""))
            path_exact = provenance_path.resolve(strict=True) == (
                expected_path.resolve(strict=True)
            )
            provenance_payload = json.loads(provenance_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValidationFailureError(
                "MR1_CANDIDATE_PROVENANCE_MANIFEST_INVALID"
            ) from exc
        if not isinstance(provenance_payload, dict):
            raise ValidationFailureError("MR1_CANDIDATE_PROVENANCE_MANIFEST_INVALID")
        provenance_core = {
            key: value
            for key, value in provenance_payload.items()
            if key != "content_hash"
        }
        items = provenance_payload.get("items") or []
        scene_ids = [item.get("scene_id") for item in items if isinstance(item, dict)]
        item_evidence_exact = len(items) == len(ALL_SCENES)
        if item_evidence_exact:
            for item in items:
                source = Path(str(item.get("source_path") or ""))
                normalized = Path(str(item.get("normalized_path") or ""))
                rights_evidence = item.get("rights") or {}
                route = item.get("route")
                rights_exact = bool(
                    (
                        route == "PEXELS_VIDEO"
                        and rights_evidence.get("rights_status") == "CONFIRMED"
                        and rights_evidence.get("provider_asset_id")
                        and rights_evidence.get("creator_ref")
                        and rights_evidence.get("source_page_url")
                        and rights_evidence.get("license_ref")
                        == "https://www.pexels.com/license/"
                    )
                    or (
                        route != "PEXELS_VIDEO"
                        and rights_evidence.get("rights_status") == "NOT_REQUIRED"
                    )
                )
                if (
                    item.get("fallback_used") is not False
                    or not rights_exact
                    or not source.is_file()
                    or source.is_symlink()
                    or not normalized.is_file()
                    or normalized.is_symlink()
                    or _sha256_file(source) != item.get("source_sha256")
                    or _sha256_file(normalized) != item.get("normalized_sha256")
                ):
                    item_evidence_exact = False
                    break
        if (
            not path_exact
            or provenance_path.is_symlink()
            or provenance_payload.get("schema_version")
            != "mr1.asset-provenance-manifest.v1"
            or provenance_payload.get("content_hash")
            != candidate.get("asset_provenance_manifest_hash")
            or provenance_payload.get("content_hash") != content_hash(provenance_core)
            or _sha256_file(provenance_path)
            != candidate.get("asset_provenance_manifest_file_sha256")
            or provenance_payload.get("timeline_hash")
            != candidate.get("canonical_timeline_hash")
            or provenance_payload.get("timeline_hash")
            != (state.get("temporal_authority") or {}).get("timeline_hash")
            or provenance_payload.get("scene_count") != len(ALL_SCENES)
            or scene_ids != list(ALL_SCENES)
            or provenance_payload.get("rights_complete") is not True
            or provenance_payload.get("provider_substitution_used") is not False
            or provenance_payload.get("automatic_fallback_used") is not False
            or not item_evidence_exact
        ):
            raise ValidationFailureError("MR1_CANDIDATE_PROVENANCE_MANIFEST_INVALID")

    def _validate_closeout_bindings(
        self,
        *,
        command: MR1FinalMediaCloseoutCommand,
        state: dict[str, Any],
        candidate: ArtifactVersion,
        candidate_content: dict[str, Any],
        drive: ArtifactVersion,
        drive_content: dict[str, Any],
    ) -> None:
        self._revalidate_canonical_drive_receipt(
            state=state,
            drive_content=drive_content,
        )
        output = Path(str(candidate_content.get("output_file_ref") or ""))
        blockers: list[str] = []
        current_candidate = state.get("review_media_candidate") or {}
        current_drive = state.get("drive_archive") or {}
        review_round = int(state.get("review_round") or 1)
        if (
            current_candidate.get("artifact_version_id") != str(candidate.id)
            or current_candidate.get("content_hash") != candidate.content_hash
            or current_candidate.get("review_round") != review_round
            or candidate_content.get("review_round") != review_round
        ):
            blockers.append("MR1_CLOSEOUT_CURRENT_REVIEW_ROUND_CANDIDATE_REQUIRED")
        if (
            current_drive.get("artifact_version_id") != str(drive.id)
            or current_drive.get("content_hash") != drive.content_hash
            or current_drive.get("review_round") != review_round
            or drive_content.get("review_round") != review_round
        ):
            blockers.append("MR1_CLOSEOUT_CURRENT_REVIEW_ROUND_DRIVE_REQUIRED")
        if candidate_content.get("run_id") != str(command.run_id):
            blockers.append("MR1_CLOSEOUT_CANDIDATE_RUN_MISMATCH")
        if candidate_content.get("output_sha256") != command.reviewed_output_sha256:
            blockers.append("MR1_CLOSEOUT_REVIEWED_SHA256_HASH_MISMATCH")
        if (
            not output.is_file()
            or _sha256_file(output) != command.reviewed_output_sha256
        ):
            blockers.append("MR1_CLOSEOUT_ACTUAL_FILE_HASH_MISMATCH")
        if candidate_content.get("technical_qc_result") != "PASS":
            blockers.append("MR1_CLOSEOUT_TECHNICAL_QC_PASS_REQUIRED")
        if candidate_content.get("creative_review_result") not in {
            "PASS",
            "ACCEPTED",
            "REVIEW_REQUIRED",
        }:
            blockers.append("MR1_CLOSEOUT_CREATIVE_REVIEW_ACCEPTANCE_REQUIRED")
        for key in (
            "production_eligible",
            "not_publishable",
            "package_lineage_valid",
            "provenance_complete",
            "rights_disclosure_resolved",
        ):
            if candidate_content.get(key) is not True:
                blockers.append(f"MR1_CLOSEOUT_CANDIDATE_{key.upper()}_REQUIRED")
        if drive_content.get("run_id") != str(command.run_id):
            blockers.append("MR1_CLOSEOUT_DRIVE_RUN_BINDING_INVALID")
        if drive_content.get("archive_identity") != command.archive_identity:
            blockers.append("MR1_CLOSEOUT_ARCHIVE_IDENTITY_MISMATCH")
        if (
            drive_content.get("status") != "VERIFIED"
            or drive_content.get("archive_state", "VERIFIED") != "VERIFIED"
            or drive_content.get("ARCHIVE_VERIFIED") is not True
        ):
            blockers.append("MR1_CLOSEOUT_DRIVE_ARCHIVE_NOT_VERIFIED")
        if drive_content.get("output_sha256") != command.reviewed_output_sha256:
            blockers.append("MR1_CLOSEOUT_DRIVE_OUTPUT_SHA256_MISMATCH")
        if (
            drive_content.get("review_media_candidate_artifact_version_id")
            != str(candidate.id)
            or drive_content.get("review_media_candidate_content_hash")
            != candidate.content_hash
        ):
            blockers.append("MR1_CLOSEOUT_DRIVE_CANDIDATE_BINDING_INVALID")
        verification = drive_content.get("verification") or {}
        if not self._drive_verification_exact(verification):
            blockers.append("MR1_CLOSEOUT_DRIVE_VERIFICATION_KEY_SET_INVALID")
        expected_count = drive_content.get("expected_item_count")
        actual_count = drive_content.get("actual_item_count")
        proof = {
            "EXACT_ITEM_SET": verification.get("exact_item_set") is True
            or drive_content.get("remote_exact_set_verified") is True,
            "EXACT_ITEM_COUNT": verification.get("exact_item_count") is True
            or (
                isinstance(expected_count, int)
                and expected_count > 0
                and actual_count == expected_count
                and drive_content.get("verified_item_count") == expected_count
            ),
            "CORRECT_PARENT": verification.get("correct_parent") is True
            or drive_content.get("parent_verified") is True,
            "CORRECT_NAMES": verification.get("correct_names") is True
            or drive_content.get("names_verified") is True,
            "SIZE_VERIFIED": verification.get("size_verified") is True
            or drive_content.get("sizes_verified") is True,
            "CHECKSUM_READBACK_VERIFIED": verification.get("checksum_readback_verified")
            is True
            or drive_content.get("checksums_verified") is True,
            "DUPLICATE_ABSENCE": verification.get("duplicate_absence") is True
            or drive_content.get("duplicate_count") == 0,
        }
        for key, passed in proof.items():
            if not passed:
                blockers.append(f"MR1_CLOSEOUT_DRIVE_{key}_REQUIRED")
        if state.get("youtube_calls") != 0:
            blockers.append("MR1_CLOSEOUT_YOUTUBE_BOUNDARY_VIOLATED")
        if blockers:
            raise ValidationFailureError(";".join(sorted(set(blockers))))

    def _revalidate_canonical_drive_receipt(
        self,
        *,
        state: dict[str, Any],
        drive_content: dict[str, Any],
    ) -> None:
        manifest_ref = state.get("final_drive_request_manifest") or {}
        raw_path = manifest_ref.get("path")
        try:
            manifest_path = Path(str(raw_path or ""))
            expected_path = (
                Path(state["workspace"])
                / "execution_evidence"
                / (
                    "final-drive-request-manifest-"
                    f"r{int(state.get('review_round') or 1):02d}.json"
                )
            )
            path_exact = manifest_path.resolve(strict=True) == (
                expected_path.resolve(strict=True)
            )
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValidationFailureError(
                "MR1_CANONICAL_DRIVE_MANIFEST_INVALID"
            ) from exc
        if not isinstance(manifest, dict):
            raise ValidationFailureError("MR1_CANONICAL_DRIVE_MANIFEST_INVALID")
        try:
            recomputed = self._validate_drive_receipt_proof(
                state=state,
                request_manifest=manifest,
                final_manifest=manifest,
                receipt=drive_content,
            )
        except (KeyError, RuntimeError, TypeError, ValueError) as exc:
            failed_keys: set[str] = set()
            if isinstance(exc, RuntimeError):
                _, separator, raw_failed = str(exc).partition(":")
                if separator:
                    failed_keys.update(
                        key for key in raw_failed.split(",") if key
                    )
            raise ValidationFailureError(
                self._canonical_drive_receipt_proof_error(
                    drive_content=drive_content,
                    failed_keys=failed_keys,
                )
            ) from exc
        if (
            not path_exact
            or manifest_path.is_symlink()
            or manifest.get("schema_version") != "mr1.final-drive-request-manifest.v1"
            or manifest.get("run_id") != state["run_id"]
            or manifest.get("archive_identity") != state["archive_identity"]
            or manifest.get("review_round") != int(state.get("review_round") or 1)
            or manifest.get("review_media_candidate")
            != state.get("review_media_candidate")
            or content_hash(manifest) != manifest_ref.get("content_hash")
            or manifest.get("item_set_hash") != manifest_ref.get("item_set_hash")
            or manifest.get("item_count") != manifest_ref.get("item_count")
        ):
            raise ValidationFailureError("MR1_CANONICAL_DRIVE_RECEIPT_PROOF_INVALID")
        if (
            drive_content.get("verification") != recomputed
            or not self._drive_verification_exact(recomputed)
        ):
            raise ValidationFailureError(
                self._canonical_drive_receipt_proof_error(
                    drive_content=drive_content,
                    failed_keys={
                        key
                        for key in DRIVE_VERIFICATION_KEYS
                        if recomputed.get(key) is not True
                    },
                )
            )

    @staticmethod
    def _canonical_drive_receipt_proof_error(
        *,
        drive_content: dict[str, Any],
        failed_keys: set[str],
    ) -> str:
        """Preserve the specific failed archive invariant at the API boundary."""

        failed = set(failed_keys)
        verification = drive_content.get("verification")
        if isinstance(verification, dict):
            failed.update(
                key
                for key in DRIVE_VERIFICATION_KEYS
                if verification.get(key) is not True
            )
        if (
            drive_content.get("status") != "VERIFIED"
            or drive_content.get("archive_state") != "VERIFIED"
            or drive_content.get("ARCHIVE_VERIFIED") is not True
        ):
            failed.add("provider_archive_state_verified")

        prefix = "MR1_CANONICAL_DRIVE_RECEIPT_PROOF_INVALID"
        if "provider_archive_state_verified" in failed:
            return f"{prefix}:MR1_DRIVE_ARCHIVE_NOT_VERIFIED"
        if "checksum_readback_verified" in failed:
            return f"{prefix}:MR1_DRIVE_ARCHIVE_CHECKSUM_READBACK_FAILED"
        if failed:
            return (
                f"{prefix}:MR1_DRIVE_ARCHIVE_PROOF_INCOMPLETE:"
                + ",".join(sorted(failed))
            )
        return prefix

    def _revalidate_final_media_lineage_authority(
        self, state: dict[str, Any]
    ) -> dict[str, Any]:
        frozen = deepcopy(state.get("final_media_lineage_authority") or {})
        supplied_hash = frozen.pop("content_hash", None)
        if (
            frozen.get("schema_version") != "mr1.final-media-lineage-authority.v1"
            or not isinstance(supplied_hash, str)
            or supplied_hash != content_hash(frozen)
        ):
            raise ValidationFailureError("MR1_FINAL_LINEAGE_FROZEN_AUTHORITY_INVALID")
        frozen["content_hash"] = supplied_hash
        project_id = uuid.UUID(state["project_id"])
        package_ref = frozen.get("package") or {}
        try:
            package = self._exact_version(
                uuid.UUID(str(package_ref["artifact_version_id"])),
                str(package_ref["content_hash"]),
                "package_manifest",
                project_id,
                status_policy="approved_package",
            )
            approval = self.session.get(
                ApprovalDecision,
                uuid.UUID(str((frozen.get("approval") or {})["approval_decision_id"])),
            )
            profile = self.session.get(
                ChannelProfileVersion,
                uuid.UUID(str(frozen["channel_profile_version"]["id"])),
            )
            snapshot = self.session.get(
                CompiledChannelPolicySnapshot,
                uuid.UUID(str(frozen["compiled_channel_policy_snapshot"]["id"])),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValidationFailureError(
                "MR1_FINAL_LINEAGE_AUTHORITY_REF_INVALID"
            ) from exc
        project = self.session.get(VideoProject, project_id)
        channel = (
            self.session.get(ChannelWorkspace, project.channel_workspace_id)
            if project is not None
            else None
        )
        profile_ref = frozen["channel_profile_version"]
        snapshot_ref = frozen["compiled_channel_policy_snapshot"]
        current_bindings = state.get("exact_bindings") or {}
        approval_ref = frozen.get("approval") or {}
        if (
            project is None
            or project.status != "approved"
            or channel is None
            or channel.status != "active"
            or profile is None
            or snapshot is None
            or channel.active_policy_snapshot_id != snapshot.id
            or package.id != uuid.UUID(state["package_artifact_version_id"])
            or package.content_hash != state["package_content_hash"]
            or approval is None
            or approval.id != uuid.UUID(state["approval_id"])
            or approval.decision != "approved"
            or approval.target_artifact_version_id != package.id
            or (approval.metadata_ or {}).get("approval_scope") != APPROVAL_SCOPE
            or approval_ref.get("approval_content_hash")
            != state["approval_content_hash"]
            or profile.status not in {"active", "approved"}
            or snapshot.status != "active"
            or profile.channel_workspace_id != channel.id
            or snapshot.channel_workspace_id != channel.id
            or project.channel_profile_version_id != profile.id
            or project.policy_snapshot_id != snapshot.id
            or str(profile.id) != state["profile_id"]
            or str(snapshot.id) != state["snapshot_id"]
            or profile.profile_input_hash != profile_ref.get("content_hash")
            or content_hash(profile.profile_input) != profile.profile_input_hash
            or snapshot.content_hash != snapshot_ref.get("content_hash")
            or content_hash(snapshot.compiled_payload) != snapshot.content_hash
            or snapshot.channel_profile_version_id != profile.id
            or snapshot.profile_input_hash != profile.profile_input_hash
            or current_bindings.get("channel_profile_version") != profile_ref
            or current_bindings.get("compiled_channel_policy_snapshot") != snapshot_ref
        ):
            raise ValidationFailureError(
                "MR1_FINAL_LINEAGE_PROFILE_PACKAGE_AUTHORITY_INVALID"
            )

        artifact_types = {
            "target_market_profile": "target_market_profile",
            "target_market_digest": "target_market_digest",
            "market_alignment_dossier": "market_alignment_dossier",
            "niche_alignment_dossier": "niche_alignment_dossier",
            "supplemental_visual_alignment": "market_gate_results",
        }
        for key, artifact_type in artifact_types.items():
            if key not in frozen:
                if key == "supplemental_visual_alignment":
                    continue
                raise ValidationFailureError(
                    f"MR1_FINAL_LINEAGE_AUTHORITY_MISSING:{key}"
                )
            ref = frozen[key]
            try:
                version = self._exact_version(
                    uuid.UUID(str(ref["artifact_version_id"])),
                    str(ref["content_hash"]),
                    artifact_type,
                    project_id,
                    allow_cross_project=True,
                    status_policy="package_authority",
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise ValidationFailureError(
                    f"MR1_FINAL_LINEAGE_AUTHORITY_REF_INVALID:{key}"
                ) from exc
            if (
                str(version.artifact_id) != ref.get("artifact_id")
                or ref.get("artifact_version_ref") != f"artifact-version://{version.id}"
                or version.version_number != ref.get("version_number")
            ):
                raise ValidationFailureError(
                    f"MR1_FINAL_LINEAGE_AUTHORITY_BINDING_INVALID:{key}"
                )
        return frozen

    @staticmethod
    def _exact_drive_final_media_proof(
        *,
        candidate_content: dict[str, Any],
        drive_content: dict[str, Any],
    ) -> dict[str, Any]:
        items = [
            item
            for item in drive_content.get("items") or []
            if isinstance(item, dict)
            and item.get("logical_role") == "MR1_FINAL_REVIEW_MP4"
        ]
        proofs = [
            item
            for item in drive_content.get("files") or []
            if isinstance(item, dict)
            and item.get("logical_role") == "MR1_FINAL_REVIEW_MP4"
        ]
        if len(items) != 1 or len(proofs) != 1:
            raise ValidationFailureError("MR1_EXACT_DRIVE_FINAL_MEDIA_ITEM_REQUIRED")
        item = items[0]
        proof = proofs[0]
        output = Path(str(candidate_content.get("output_file_ref") or ""))
        drive_file_id = str(proof.get("drive_file_id") or "")
        local_sha = candidate_content.get("output_sha256")
        actual_md5 = _md5_file(output) if output.is_file() else None
        remote_sha = proof.get("remote_sha256")
        checksum_exact = bool(
            proof.get("local_sha256") == local_sha
            and (
                remote_sha == local_sha
                or (
                    proof.get("local_md5") == actual_md5
                    and proof.get("remote_md5") == actual_md5
                )
            )
        )
        try:
            same_path = Path(str(item.get("source_path") or "")).resolve(
                strict=True
            ) == output.resolve(strict=True)
        except OSError:
            same_path = False
        if (
            not output.is_file()
            or output.is_symlink()
            or not same_path
            or _sha256_file(output) != local_sha
            or item.get("sha256") != local_sha
            or item.get("size_bytes") != output.stat().st_size
            or item.get("name") != proof.get("name")
            or item.get("archive_path") != proof.get("archive_path")
            or proof.get("local_size_bytes") != output.stat().st_size
            or proof.get("remote_size_bytes") != output.stat().st_size
            or proof.get("drive_folder_id") != drive_content.get("drive_folder_id")
            or proof.get("verified") is not True
            or proof.get("verification_method")
            not in {"SHA256_PLUS_SIZE", "MD5_PLUS_SIZE"}
            or not checksum_exact
            or not re.fullmatch(r"[A-Za-z0-9_-]+", drive_file_id)
            or not str(proof.get("name") or "").endswith(".mp4")
        ):
            raise ValidationFailureError("MR1_DRIVE_FINAL_MEDIA_PROOF_INVALID")
        return {
            "logical_role": "MR1_FINAL_REVIEW_MP4",
            "name": proof["name"],
            "archive_path": proof["archive_path"],
            "drive_file_id": drive_file_id,
            "drive_folder_id": proof["drive_folder_id"],
            "local_size_bytes": proof["local_size_bytes"],
            "remote_size_bytes": proof["remote_size_bytes"],
            "local_sha256": proof["local_sha256"],
            "remote_sha256": remote_sha,
            "local_md5": proof.get("local_md5"),
            "remote_md5": proof.get("remote_md5"),
            "verification_method": proof["verification_method"],
            "verified": True,
            "web_view_link": (f"https://drive.google.com/file/d/{drive_file_id}/view"),
            "mime_type": "video/mp4",
        }

    @staticmethod
    def _final_media_source_refs(
        *,
        candidate: ArtifactVersion,
        drive: ArtifactVersion,
        technical_qc: ArtifactVersion,
        human_receipt: ArtifactVersion,
        frozen_lineage: dict[str, Any],
    ) -> list[dict[str, Any]]:
        refs = [
            {
                "type": CANDIDATE_ARTIFACT_TYPE,
                "artifact_version_id": str(candidate.id),
                "content_hash": candidate.content_hash,
            },
            {
                "type": DRIVE_RECEIPT_ARTIFACT_TYPE,
                "artifact_version_id": str(drive.id),
                "content_hash": drive.content_hash,
            },
            {
                "type": TECHNICAL_QC_ARTIFACT_TYPE,
                "artifact_version_id": str(technical_qc.id),
                "content_hash": technical_qc.content_hash,
            },
            {
                "type": HUMAN_RECEIPT_ARTIFACT_TYPE,
                "artifact_version_id": str(human_receipt.id),
                "content_hash": human_receipt.content_hash,
            },
            {"type": "package_manifest", **deepcopy(frozen_lineage["package"])},
            {"type": "mr1_approval", **deepcopy(frozen_lineage["approval"])},
            {
                "type": "channel_profile_version",
                **deepcopy(frozen_lineage["channel_profile_version"]),
            },
            {
                "type": "compiled_channel_policy_snapshot",
                **deepcopy(frozen_lineage["compiled_channel_policy_snapshot"]),
            },
        ]
        for key in (
            "target_market_profile",
            "target_market_digest",
            "market_alignment_dossier",
            "niche_alignment_dossier",
            "supplemental_visual_alignment",
        ):
            if key in frozen_lineage:
                refs.append({"type": key, **deepcopy(frozen_lineage[key])})
        return refs

    def _create_final_cloud_media_ref(
        self,
        *,
        state: dict[str, Any],
        project: VideoProject,
        candidate: ArtifactVersion,
        drive: ArtifactVersion,
        technical_qc: ArtifactVersion,
        human_receipt: ArtifactVersion,
        frozen_lineage: dict[str, Any],
        final_drive_proof: dict[str, Any],
    ) -> CloudMediaRef:
        source_refs = self._final_media_source_refs(
            candidate=candidate,
            drive=drive,
            technical_qc=technical_qc,
            human_receipt=human_receipt,
            frozen_lineage=frozen_lineage,
        )
        output_path = Path(str((candidate.content or {})["output_file_ref"]))
        return CloudMediaRefService(self.session).create_verified_ref(
            company_id=project.company_id,
            channel_workspace_id=project.channel_workspace_id,
            video_project_id=project.id,
            uploaded_video_id=None,
            render_package_id=None,
            media_type="LONG_FORM_FINAL",
            upload_result=GoogleDriveUploadResult(
                drive_file_id=final_drive_proof["drive_file_id"],
                drive_folder_id=final_drive_proof["drive_folder_id"],
                web_view_link=final_drive_proof["web_view_link"],
                file_name=final_drive_proof["name"],
                mime_type=final_drive_proof["mime_type"],
                size_bytes=final_drive_proof["remote_size_bytes"],
                checksum_sha256=final_drive_proof["remote_sha256"],
                upload_mode="MR1_VERIFIED_ARCHIVE_RECEIPT",
                technical_appendix={
                    "run_id": state["run_id"],
                    "archive_identity": state["archive_identity"],
                    "drive_receipt_artifact_version_id": str(drive.id),
                    "drive_receipt_content_hash": drive.content_hash,
                    "logical_role": "MR1_FINAL_REVIEW_MP4",
                    "verification_method": final_drive_proof["verification_method"],
                    "remote_md5": final_drive_proof["remote_md5"],
                },
            ),
            verification=GoogleDriveVerificationResult(
                ok=True,
                verification_status="CHECKSUM_VERIFIED",
                reason_code="MR1_DRIVE_FINAL_MEDIA_VERIFIED",
                size_verified=True,
                checksum_verified=True,
                checksum_unavailable=False,
            ),
            local_source_path_hash=content_hash(
                {"source_path": str(output_path.resolve(strict=True))}
            ),
            checksum_sha256=final_drive_proof["local_sha256"],
            source_refs=source_refs,
            retention_policy={
                "cleanup_after_verified": False,
                "authority": "MR1_HUMAN_PASS_FINAL_MEDIA",
                "archive_identity": state["archive_identity"],
            },
        )

    def _reopen_final_cloud_media_ref_for_resume(
        self,
        *,
        state: dict[str, Any],
        project: VideoProject,
        candidate: ArtifactVersion,
        drive: ArtifactVersion,
        technical_qc: ArtifactVersion,
        human_receipt: ArtifactVersion,
        frozen_lineage: dict[str, Any],
        final_drive_proof: dict[str, Any],
    ) -> CloudMediaRef:
        try:
            cloud_ref = self.session.get(
                CloudMediaRef,
                uuid.UUID(str(state["final_cloud_media_ref_id"])),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValidationFailureError("MR1_FINALIZATION_CLOUD_REF_INVALID") from exc
        source_refs = self._final_media_source_refs(
            candidate=candidate,
            drive=drive,
            technical_qc=technical_qc,
            human_receipt=human_receipt,
            frozen_lineage=frozen_lineage,
        )
        output_path = Path(str((candidate.content or {})["output_file_ref"]))
        expected_local_path_hash = content_hash(
            {"source_path": str(output_path.resolve(strict=True))}
        )
        expected_retention_policy = {
            "cleanup_after_verified": False,
            "authority": "MR1_HUMAN_PASS_FINAL_MEDIA",
            "archive_identity": state["archive_identity"],
        }
        expected_appendix = {
            "run_id": state["run_id"],
            "archive_identity": state["archive_identity"],
            "drive_receipt_artifact_version_id": str(drive.id),
            "drive_receipt_content_hash": drive.content_hash,
            "logical_role": "MR1_FINAL_REVIEW_MP4",
            "verification_method": final_drive_proof["verification_method"],
            "remote_md5": final_drive_proof["remote_md5"],
            "drive_file_id_verified": True,
            "size_verified": True,
            "checksum_verified": True,
            "checksum_unavailable": False,
            "dashboard_drive_cta_only": True,
        }
        if (
            cloud_ref is None
            or cloud_ref.company_id != project.company_id
            or cloud_ref.channel_workspace_id != project.channel_workspace_id
            or cloud_ref.video_project_id != project.id
            or cloud_ref.uploaded_video_id is not None
            or cloud_ref.render_package_id is not None
            or cloud_ref.media_type != "LONG_FORM_FINAL"
            or cloud_ref.storage_provider != "GOOGLE_DRIVE"
            or cloud_ref.drive_file_id != final_drive_proof["drive_file_id"]
            or cloud_ref.drive_folder_id != final_drive_proof["drive_folder_id"]
            or cloud_ref.web_view_link != final_drive_proof["web_view_link"]
            or cloud_ref.file_name != final_drive_proof["name"]
            or cloud_ref.mime_type != final_drive_proof["mime_type"]
            or cloud_ref.size_bytes != final_drive_proof["remote_size_bytes"]
            or cloud_ref.checksum_sha256 != final_drive_proof["local_sha256"]
            or cloud_ref.local_source_path_hash != expected_local_path_hash
            or cloud_ref.upload_status != "VERIFIED"
            or cloud_ref.verification_status != "CHECKSUM_VERIFIED"
            or cloud_ref.local_cleanup_status != "NOT_ELIGIBLE"
            or cloud_ref.uploaded_at is None
            or cloud_ref.cleaned_at is not None
            or cloud_ref.source_refs != source_refs
            or cloud_ref.retention_policy != expected_retention_policy
            or cloud_ref.technical_appendix != expected_appendix
        ):
            raise ValidationFailureError("MR1_FINALIZATION_CLOUD_REF_INVALID")
        return cloud_ref

    @staticmethod
    def _archived_artifact_version_payload(
        *,
        artifact_type: str,
        version: ArtifactVersion,
    ) -> dict[str, Any]:
        payload = {
            "schema_version": "mr1.archived-artifact-version.v1",
            "artifact_type": artifact_type,
            "artifact_id": str(version.artifact_id),
            "artifact_version_id": str(version.id),
            "artifact_version_ref": f"artifact-version://{version.id}",
            "artifact_content_hash": version.content_hash,
            "content": deepcopy(version.content or {}),
        }
        return {**payload, "content_hash": content_hash(payload)}

    def _finalize_archive_supplement(
        self,
        *,
        run_artifact: Artifact,
        state: dict[str, Any],
        candidate: ArtifactVersion,
        drive: ArtifactVersion,
        human_receipt: ArtifactVersion,
        final_lineage: ArtifactVersion,
        drive_gateway: Any,
        actor_id: uuid.UUID,
    ) -> ArtifactVersion:
        workspace = Path(state["workspace"]).resolve(strict=True)
        expected_drive_phases = DRIVE_IDEMPOTENCY_PHASES
        task_authorization = state.get("task_authorization") or {}
        task_authorization_core = {
            key: value
            for key, value in task_authorization.items()
            if key != "content_hash"
        }
        if (
            task_authorization.get("content_hash")
            != content_hash(task_authorization_core)
            or task_authorization.get("approval_id") != state["approval_id"]
            or task_authorization.get("approval_content_hash")
            != state["approval_content_hash"]
            or task_authorization.get("drive_idempotency_phases")
            != expected_drive_phases
            or task_authorization.get("drive_phase_count") != 2
            or task_authorization.get("drive_phases_are_distinct_authorized_mutations")
            is not True
        ):
            raise ValidationFailureError("MR1_FINALIZATION_DRIVE_PHASE_NOT_AUTHORIZED")
        operation_key = MR1_DRIVE_FINALIZATION_OPERATION_KEY
        review_round = int(state.get("review_round") or 1)
        expected_idempotency_key = mr1_drive_finalization_idempotency_key(
            run_id=state["run_id"],
            review_round=review_round,
        )
        expected_idempotency_fingerprint = _idempotency_fingerprint(
            approval_content_hash=state["approval_content_hash"],
            run_id=state["run_id"],
            provider="google_drive",
            operation="finalization_supplement",
            scene_id=None,
        )
        ledger = (state.get("attempts") or {}).get(operation_key)
        if (
            not isinstance(ledger, dict)
            or ledger.get("provider") != "google_drive"
            or ledger.get("operation") != "finalization_supplement"
            or ledger.get("attempt_cap") != 1
            or ledger.get("drive_phase_authority") != expected_drive_phases[1]
            or ledger.get("distinct_from_canonical_archive") is not True
            or ledger.get("automatic_retry_allowed") is not False
            or ledger.get("review_round") != review_round
            or ledger.get("idempotency_key") != expected_idempotency_key
            or ledger.get("idempotency_fingerprint") != expected_idempotency_fingerprint
            or operation_key not in (state.get("attempt_artifact_ids") or {})
        ):
            raise ValidationFailureError("MR1_FINALIZATION_ATTEMPT_AUTHORITY_INVALID")
        finalization_dir = workspace / "finalization"
        human_path = finalization_dir / "human-full-watch-receipt.json"
        lineage_path = finalization_dir / "final-media-lineage-receipt.json"
        _write_json_atomic(
            human_path,
            self._archived_artifact_version_payload(
                artifact_type=HUMAN_RECEIPT_ARTIFACT_TYPE,
                version=human_receipt,
            ),
        )
        _write_json_atomic(
            lineage_path,
            self._archived_artifact_version_payload(
                artifact_type=FINAL_LINEAGE_ARTIFACT_TYPE,
                version=final_lineage,
            ),
        )

        def archive_item(
            *, path: Path, logical_role: str, archive_path: str
        ) -> dict[str, Any]:
            return {
                "logical_role": logical_role,
                "name": path.name,
                "source_path": str(path.resolve(strict=True)),
                "archive_path": archive_path,
                "size_bytes": path.stat().st_size,
                "sha256": _sha256_file(path),
                "md5": _md5_file(path),
            }

        files = sorted(
            [
                archive_item(
                    path=human_path,
                    logical_role="MR1_HUMAN_FULL_WATCH_RECEIPT",
                    archive_path=("finalization/human-full-watch-receipt.json"),
                ),
                archive_item(
                    path=lineage_path,
                    logical_role="MR1_FINAL_MEDIA_LINEAGE_RECEIPT",
                    archive_path=("finalization/final-media-lineage-receipt.json"),
                ),
            ],
            key=lambda item: item["archive_path"],
        )
        manifest = {
            "schema_version": ("mr1.finalization-archive-supplement-manifest.v1"),
            "run_id": state["run_id"],
            "project_id": state["project_id"],
            "archive_identity": state["archive_identity"],
            "review_round": review_round,
            "drive_phase_authority": deepcopy(expected_drive_phases[1]),
            "idempotency_identity": {
                "operation_key": operation_key,
                "idempotency_key": expected_idempotency_key,
                "idempotency_fingerprint": expected_idempotency_fingerprint,
                "review_round": review_round,
                "distinct_from_canonical_archive": True,
                "automatic_retry_allowed": False,
            },
            "review_media_candidate": {
                "artifact_version_id": str(candidate.id),
                "content_hash": candidate.content_hash,
            },
            "canonical_drive_archive_receipt": {
                "artifact_version_id": str(drive.id),
                "content_hash": drive.content_hash,
            },
            "human_full_watch_receipt": {
                "artifact_version_id": str(human_receipt.id),
                "content_hash": human_receipt.content_hash,
            },
            "final_media_lineage_receipt": {
                "artifact_version_id": str(final_lineage.id),
                "content_hash": final_lineage.content_hash,
            },
            "item_count": len(files),
            "total_size_bytes": sum(item["size_bytes"] for item in files),
            "item_set_hash": content_hash({"files": files}),
            "files": files,
        }
        mutation_started = False

        def before_first_mutation() -> None:
            nonlocal mutation_started
            if mutation_started:
                raise RuntimeError("MR1_FINALIZATION_DUPLICATE_DRIVE_BOUNDARY")
            mutation_started = True
            attempt_count = int(ledger.get("attempt_count") or 0)
            if attempt_count == 0:
                ledger["attempt_count"] = 1
                ledger["network_submit_started"] = True
                state["provider_call_counts"]["drive"] += 1
                state["provider_call_counts"]["logical_total"] += 1
            elif attempt_count != 1 or ledger.get("network_submit_started") is not True:
                raise RuntimeError("MR1_FINALIZATION_ATTEMPT_LEDGER_CONFLICT")
            ledger["state"] = "MUTATING_OR_RECONCILING"
            ledger["submit_state"] = "SUBMITTING_OR_RECONCILING"
            ledger["request_hash"] = content_hash(manifest)
            state["final_archive_supplement_attempt"] = {
                "schema_version": ("mr1.finalization-archive-supplement-attempt.v1"),
                "state": "MUTATING_OR_RECONCILING",
                "manifest_hash": content_hash(manifest),
                "item_set_hash": manifest["item_set_hash"],
                "canonical_drive_archive_receipt": deepcopy(
                    manifest["canonical_drive_archive_receipt"]
                ),
                "idempotency_identity": deepcopy(manifest["idempotency_identity"]),
                "mutation_boundary_declared": True,
            }
            self._save_attempt(state, operation_key, actor_id)
            self._save_run(run_artifact, state, actor_id=actor_id)
            self._durable_boundary()

        remote_receipt = drive_gateway.upload_finalization_supplement_and_verify(
            manifest=deepcopy(manifest),
            archive_identity=state["archive_identity"],
            journal_path=(workspace / "drive" / "finalization-remote-id-journal.json"),
            before_first_mutation=before_first_mutation,
        )
        if not mutation_started:
            raise ValidationFailureError("MR1_FINALIZATION_DRIVE_BOUNDARY_NOT_DECLARED")
        try:
            supplement_verification = self._validate_drive_receipt_proof(
                state=state,
                request_manifest=manifest,
                final_manifest=manifest,
                receipt=remote_receipt,
            )
        except (KeyError, RuntimeError, TypeError, ValueError) as exc:
            raise ValidationFailureError(
                "MR1_FINALIZATION_DRIVE_RECEIPT_INVALID"
            ) from exc
        remote_receipt = {
            **remote_receipt,
            "verification": supplement_verification,
        }
        if (
            not isinstance(remote_receipt, dict)
            or remote_receipt.get("archive_phase") != "FINALIZATION_SUPPLEMENT"
            or remote_receipt.get("run_id") != state["run_id"]
            or remote_receipt.get("archive_identity") != state["archive_identity"]
            or remote_receipt.get("ARCHIVE_VERIFIED") is not True
            or remote_receipt.get("archive_state") != "VERIFIED"
            or remote_receipt.get("expected_item_count") != len(files)
            or remote_receipt.get("verified_item_count") != len(files)
            or remote_receipt.get("remote_item_count") != len(files)
            or remote_receipt.get("supplement_manifest_hash") != content_hash(manifest)
            or remote_receipt.get("supplement_item_set_hash")
            != manifest["item_set_hash"]
            or remote_receipt.get("canonical_drive_archive_receipt")
            != manifest["canonical_drive_archive_receipt"]
            or not self._drive_receipt_hash_valid(remote_receipt)
            or not self._drive_verification_exact(remote_receipt.get("verification"))
        ):
            raise ValidationFailureError("MR1_FINALIZATION_DRIVE_RECEIPT_INVALID")
        receipt_payload = {
            "schema_version": ("mr1.drive-finalization-supplement-receipt.v1"),
            "run_id": state["run_id"],
            "project_id": state["project_id"],
            "archive_identity": state["archive_identity"],
            "review_round": manifest["review_round"],
            "review_media_candidate": deepcopy(manifest["review_media_candidate"]),
            "canonical_drive_archive_receipt": deepcopy(
                manifest["canonical_drive_archive_receipt"]
            ),
            "human_full_watch_receipt": deepcopy(manifest["human_full_watch_receipt"]),
            "final_media_lineage_receipt": deepcopy(
                manifest["final_media_lineage_receipt"]
            ),
            "supplement_manifest": deepcopy(manifest),
            "remote_verification_receipt": deepcopy(remote_receipt),
            "exact_supplement_item_set_verified": True,
            "canonical_review_archive_mutated": False,
            "final_media_registration_allowed": True,
        }
        existing = self._find_artifact_for_run(
            uuid.UUID(state["run_id"]),
            FINAL_ARCHIVE_SUPPLEMENT_ARTIFACT_TYPE,
        )
        if existing is not None:
            if existing.content == receipt_payload:
                return existing
            raise ValidationFailureError(
                "MR1_FINALIZATION_SUPPLEMENT_IDEMPOTENCY_CONFLICT"
            )
        ledger["state"] = "SUCCEEDED"
        ledger["submit_state"] = "SUCCEEDED"
        ledger["output"] = {
            "artifact_type": FINAL_ARCHIVE_SUPPLEMENT_ARTIFACT_TYPE,
            "remote_receipt_hash": remote_receipt["receipt_hash"],
            "supplement_manifest_hash": content_hash(manifest),
            "supplement_item_set_hash": manifest["item_set_hash"],
            "archive_state": "VERIFIED",
        }
        self._save_attempt(state, operation_key, actor_id)
        _, version = self._create_artifact(
            project_id=uuid.UUID(state["project_id"]),
            artifact_type=FINAL_ARCHIVE_SUPPLEMENT_ARTIFACT_TYPE,
            actor_id=actor_id,
            content=receipt_payload,
            correlation_id=(
                f"mr1-finalization-supplement-{state['run_id']}-"
                f"round-{manifest['review_round']}"
            ),
        )
        state["final_archive_supplement_attempt"] = {
            **deepcopy(state["final_archive_supplement_attempt"]),
            "state": "VERIFIED",
            "remote_receipt_hash": remote_receipt["receipt_hash"],
            "artifact_version_id": str(version.id),
            "content_hash": version.content_hash,
        }
        return version

    def _persist_final_media_lineage(
        self,
        *,
        state: dict[str, Any],
        candidate: ArtifactVersion,
        candidate_content: dict[str, Any],
        drive: ArtifactVersion,
        technical_qc: ArtifactVersion,
        human_receipt: ArtifactVersion,
        cloud_ref: CloudMediaRef,
        frozen_lineage: dict[str, Any],
        final_drive_proof: dict[str, Any],
        actor_id: uuid.UUID,
    ) -> ArtifactVersion:
        source_refs = self._final_media_source_refs(
            candidate=candidate,
            drive=drive,
            technical_qc=technical_qc,
            human_receipt=human_receipt,
            frozen_lineage=frozen_lineage,
        )
        payload = {
            "schema_version": "mr1.final-media-lineage-receipt.v1",
            "run_id": state["run_id"],
            "project_id": state["project_id"],
            "review_round": int(state.get("review_round") or 1),
            "output_sha256": candidate_content["output_sha256"],
            "output_size_bytes": Path(str(candidate_content["output_file_ref"]))
            .stat()
            .st_size,
            "review_media_candidate": {
                "artifact_version_id": str(candidate.id),
                "content_hash": candidate.content_hash,
            },
            "drive_archive_receipt": {
                "artifact_version_id": str(drive.id),
                "content_hash": drive.content_hash,
                "archive_identity": state["archive_identity"],
            },
            "drive_final_media_proof": deepcopy(final_drive_proof),
            "cloud_media_ref": {
                "id": str(cloud_ref.id),
                "storage_provider": cloud_ref.storage_provider,
                "drive_file_id": cloud_ref.drive_file_id,
                "drive_folder_id": cloud_ref.drive_folder_id,
                "web_view_link": cloud_ref.web_view_link,
                "file_name": cloud_ref.file_name,
                "mime_type": cloud_ref.mime_type,
                "size_bytes": cloud_ref.size_bytes,
                "checksum_sha256": cloud_ref.checksum_sha256,
                "upload_status": cloud_ref.upload_status,
                "verification_status": cloud_ref.verification_status,
                "source_refs_hash": content_hash({"source_refs": source_refs}),
            },
            "technical_media_qc": {
                "artifact_version_id": str(technical_qc.id),
                "content_hash": technical_qc.content_hash,
                "result": "PASS",
            },
            "human_full_watch_receipt": {
                "artifact_version_id": str(human_receipt.id),
                "content_hash": human_receipt.content_hash,
                "decision": "PASS",
            },
            "drive_finalization_authority": {
                "phase": deepcopy(
                    state["task_authorization"]["drive_idempotency_phases"][1]
                ),
                "distinct_from_canonical_archive": True,
                "verified_supplement_required_before_final_media_ref": True,
            },
            "frozen_authority": deepcopy(frozen_lineage),
            "source_refs": source_refs,
            "provider_key": "mr1-native-ffmpeg-renderer",
            "provider_type": "LOCAL_RENDERER_CAPABILITY",
            "media_qc_report_id": None,
            "production_eligible": True,
            "human_pass_required_and_present": True,
            "publish_execution_authorized": False,
            "youtube_calls": 0,
        }
        existing = self._find_artifact_for_run(
            uuid.UUID(state["run_id"]), FINAL_LINEAGE_ARTIFACT_TYPE
        )
        if existing is not None:
            if existing.content == payload:
                return existing
            raise ValidationFailureError("MR1_FINAL_MEDIA_LINEAGE_IDEMPOTENCY_CONFLICT")
        _, version = self._create_artifact(
            project_id=uuid.UUID(state["project_id"]),
            artifact_type=FINAL_LINEAGE_ARTIFACT_TYPE,
            actor_id=actor_id,
            content=payload,
            correlation_id=f"mr1-final-media-lineage-{state['run_id']}",
        )
        return version

    def _require_final_human_review_task(
        self,
        *,
        state: dict[str, Any],
        candidate: ArtifactVersion,
        drive: ArtifactVersion,
        decided_by_user_id: uuid.UUID,
        allow_completed: bool,
    ) -> ReviewTask:
        try:
            task = self.session.get(
                ReviewTask,
                uuid.UUID(str(state["final_human_review_task_id"])),
            )
        except (KeyError, TypeError, ValueError):
            task = None
        review_round = int(state.get("review_round") or 1)
        status_allowed = (
            {"open", "in_progress", "completed"}
            if allow_completed
            else {"open", "in_progress"}
        )
        evidence = list(task.evidence_refs or []) if task is not None else []
        candidate_evidence = [
            item
            for item in evidence
            if item.get("type") == "mr1_review_media_candidate"
        ]
        drive_evidence = [
            item for item in evidence if item.get("type") == "mr1_drive_archive_receipt"
        ]
        package_evidence = [
            item for item in evidence if item.get("type") == "mr1_exact_package"
        ]
        technical_evidence = [
            item
            for item in evidence
            if item.get("type") == "mr1_technical_media_qc_receipt"
        ]
        technical_ref = state.get("technical_media_qc") or {}
        expected_reason_codes = self._final_human_review_reason_codes(review_round)
        expected_context_ref = self._final_human_review_context_ref(state, review_round)
        resolution_evidence = [
            item
            for item in evidence
            if item.get("type") == "explicit_human_approval_resolution"
        ]
        completed_semantics_valid = True
        if task is not None and task.status == "completed":
            completed_semantics_valid = bool(
                len(resolution_evidence) == 1
                and resolution_evidence[0].get("approval_decision_ids")
                == [state["approval_id"]]
                and resolution_evidence[0].get("prior_review_reason_codes_retained")
                == expected_reason_codes
                and isinstance(resolution_evidence[0].get("resolution_ref"), str)
                and resolution_evidence[0]["resolution_ref"].startswith(
                    "artifact-version://"
                )
            )
        if (
            task is None
            or task.video_project_id != uuid.UUID(state["project_id"])
            or task.target_type != "artifact_version"
            or task.review_type != "final_human"
            or task.status not in status_allowed
            or task.target_id != candidate.id
            or task.target_artifact_version_id != candidate.id
            or task.assigned_to_user_id != decided_by_user_id
            or task.requested_by_user_id != decided_by_user_id
            or state.get("final_human_review_assigned_to_user_id")
            != str(decided_by_user_id)
            or task.evidence_required is not True
            or task.review_reason_codes != expected_reason_codes
            or task.context_pack_ref != expected_context_ref
            or not completed_semantics_valid
            or len(candidate_evidence) != 1
            or candidate_evidence[0].get("artifact_version_id") != str(candidate.id)
            or candidate_evidence[0].get("content_hash") != candidate.content_hash
            or candidate_evidence[0].get("review_round") != review_round
            or len(drive_evidence) != 1
            or drive_evidence[0].get("artifact_version_id") != str(drive.id)
            or drive_evidence[0].get("content_hash") != drive.content_hash
            or drive_evidence[0].get("review_round") != review_round
            or len(package_evidence) != 1
            or package_evidence[0].get("artifact_version_id")
            != state["package_artifact_version_id"]
            or package_evidence[0].get("content_hash") != state["package_content_hash"]
            or len(technical_evidence) != 1
            or technical_evidence[0].get("artifact_version_id")
            != technical_ref.get("artifact_version_id")
            or technical_evidence[0].get("content_hash")
            != technical_ref.get("content_hash")
            or technical_evidence[0].get("review_round") != review_round
        ):
            raise ValidationFailureError(
                "MR1_FINAL_HUMAN_REVIEW_TASK_AUTHORITY_INVALID"
            )
        return task

    def _read_existing_closeout(
        self,
        state: dict[str, Any],
        command: MR1FinalMediaCloseoutCommand,
    ) -> dict[str, Any]:
        receipt = self._find_human_receipt(command.run_id)
        if receipt is None:
            raise ValidationFailureError("MR1_CLOSEOUT_RECEIPT_MISSING")
        receipt = self._exact_version(
            receipt.id,
            receipt.content_hash,
            HUMAN_RECEIPT_ARTIFACT_TYPE,
            command.project_id,
        )
        candidate = self._exact_version(
            command.review_media_candidate_artifact_version_id,
            command.review_media_candidate_content_hash,
            CANDIDATE_ARTIFACT_TYPE,
            command.project_id,
        )
        drive = self._exact_version(
            command.drive_archive_receipt_artifact_version_id,
            command.drive_archive_receipt_content_hash,
            DRIVE_RECEIPT_ARTIFACT_TYPE,
            command.project_id,
        )
        candidate_content = deepcopy(candidate.content or {})
        drive_content = deepcopy(drive.content or {})
        self._validate_candidate_payload(state, candidate_content)
        technical_qc = self._require_technical_qc_authority(
            state=state,
            candidate=candidate,
            candidate_content=candidate_content,
        )
        self._revalidate_canonical_drive_receipt(
            state=state,
            drive_content=drive_content,
        )
        self._validate_closeout_bindings(
            command=command,
            state=state,
            candidate=candidate,
            candidate_content=candidate_content,
            drive=drive,
            drive_content=drive_content,
        )
        task = self._require_final_human_review_task(
            state=state,
            candidate=candidate,
            drive=drive,
            decided_by_user_id=command.decided_by_user_id,
            allow_completed=True,
        )
        body = receipt.content or {}
        receipt_candidate = body.get("review_media_candidate") or {}
        receipt_drive = body.get("drive_archive_receipt") or {}
        receipt_technical = body.get("technical_media_qc") or {}
        receipt_task = body.get("final_human_review_task") or {}
        if (
            body.get("decision") != command.decision
            or body.get("reviewed_output_sha256") != command.reviewed_output_sha256
            or body.get("archive_identity") != command.archive_identity
            or body.get("review_round") != int(state.get("review_round") or 1)
            or body.get("decided_by_user_id") != str(command.decided_by_user_id)
            or receipt_candidate.get("artifact_version_id") != str(candidate.id)
            or receipt_candidate.get("content_hash") != candidate.content_hash
            or receipt_drive.get("artifact_version_id") != str(drive.id)
            or receipt_drive.get("content_hash") != drive.content_hash
            or receipt_technical.get("artifact_version_id") != str(technical_qc.id)
            or receipt_technical.get("content_hash") != technical_qc.content_hash
            or receipt_technical.get("result") != "PASS"
            or receipt_task.get("review_task_id") != str(task.id)
            or receipt_task.get("assigned_to_user_id")
            != str(command.decided_by_user_id)
            or task.status != "completed"
            or not any(
                item.get("type") == "explicit_human_approval_resolution"
                and item.get("resolution_ref")
                == self._human_review_resolution_ref(receipt)
                and item.get("approval_decision_ids") == [state["approval_id"]]
                for item in task.evidence_refs or []
            )
            or not any(
                item.get("type") == "mr1_human_full_watch_receipt"
                and item.get("artifact_version_id") == str(receipt.id)
                and item.get("content_hash") == receipt.content_hash
                and item.get("decision") == command.decision
                and item.get("decided_by_user_id") == str(command.decided_by_user_id)
                and item.get("review_round") == int(state.get("review_round") or 1)
                and item.get("resolution_ref")
                == self._human_review_resolution_ref(receipt)
                for item in task.evidence_refs or []
            )
        ):
            raise ValidationFailureError("MR1_CLOSEOUT_IDEMPOTENCY_CONFLICT")
        if command.decision == "PASS":
            final_ref = self._require_existing_final_media_authority(
                state=state,
                candidate=candidate,
                candidate_content=candidate_content,
                drive=drive,
                drive_content=drive_content,
                technical_qc=technical_qc,
                human_receipt=receipt,
            )
            state["final_media_ref_id"] = str(final_ref.id)
            state["current_state"] = "FINAL_MEDIA_REGISTERED"
        _, version = self._require_run(command.run_id)
        return self._public_result(version, state)

    def _require_existing_final_media_authority(
        self,
        *,
        state: dict[str, Any],
        candidate: ArtifactVersion,
        candidate_content: dict[str, Any],
        drive: ArtifactVersion,
        drive_content: dict[str, Any],
        technical_qc: ArtifactVersion,
        human_receipt: ArtifactVersion,
    ) -> FinalMediaRef:
        frozen_lineage = self._revalidate_final_media_lineage_authority(state)
        project = self.session.get(VideoProject, uuid.UUID(state["project_id"]))
        if project is None:
            raise ValidationFailureError("MR1_FINAL_MEDIA_AUTHORITY_INVALID")
        final_drive_proof = self._exact_drive_final_media_proof(
            candidate_content=candidate_content,
            drive_content=drive_content,
        )
        lineage_ref = state.get("final_media_lineage") or {}
        try:
            lineage = self._exact_version(
                uuid.UUID(str(lineage_ref["artifact_version_id"])),
                str(lineage_ref["content_hash"]),
                FINAL_LINEAGE_ARTIFACT_TYPE,
                uuid.UUID(state["project_id"]),
            )
            cloud_ref = self.session.get(
                CloudMediaRef,
                uuid.UUID(str(state["final_cloud_media_ref_id"])),
            )
            final_ref = self.session.get(
                FinalMediaRef,
                uuid.UUID(str(state["final_media_ref_id"])),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValidationFailureError(
                "MR1_FINAL_MEDIA_AUTHORITY_REF_INVALID"
            ) from exc
        if cloud_ref is None or final_ref is None:
            raise ValidationFailureError("MR1_FINAL_MEDIA_AUTHORITY_MISSING")
        self._revalidate_final_archive_supplement(
            state=state,
            candidate=candidate,
            drive=drive,
            human_receipt=human_receipt,
            final_lineage=lineage,
        )
        source_refs = self._final_media_source_refs(
            candidate=candidate,
            drive=drive,
            technical_qc=technical_qc,
            human_receipt=human_receipt,
            frozen_lineage=frozen_lineage,
        )
        output = Path(str(candidate_content.get("output_file_ref") or ""))
        expected_local_path_hash = (
            content_hash({"source_path": str(output.resolve(strict=True))})
            if output.is_file()
            else None
        )
        expected_retention_policy = {
            "cleanup_after_verified": False,
            "authority": "MR1_HUMAN_PASS_FINAL_MEDIA",
            "archive_identity": state["archive_identity"],
        }
        expected_cloud_appendix = {
            "run_id": state["run_id"],
            "archive_identity": state["archive_identity"],
            "drive_receipt_artifact_version_id": str(drive.id),
            "drive_receipt_content_hash": drive.content_hash,
            "logical_role": "MR1_FINAL_REVIEW_MP4",
            "verification_method": final_drive_proof["verification_method"],
            "remote_md5": final_drive_proof["remote_md5"],
            "drive_file_id_verified": True,
            "size_verified": True,
            "checksum_verified": True,
            "checksum_unavailable": False,
            "dashboard_drive_cta_only": True,
        }
        cloud_exact = bool(
            cloud_ref.company_id == project.company_id
            and cloud_ref.channel_workspace_id == project.channel_workspace_id
            and cloud_ref.video_project_id == project.id
            and cloud_ref.uploaded_video_id is None
            and cloud_ref.render_package_id is None
            and cloud_ref.media_type == "LONG_FORM_FINAL"
            and cloud_ref.storage_provider == "GOOGLE_DRIVE"
            and cloud_ref.drive_file_id == final_drive_proof["drive_file_id"]
            and cloud_ref.drive_folder_id == final_drive_proof["drive_folder_id"]
            and cloud_ref.web_view_link == final_drive_proof["web_view_link"]
            and cloud_ref.file_name == final_drive_proof["name"]
            and cloud_ref.mime_type == "video/mp4"
            and cloud_ref.size_bytes == final_drive_proof["remote_size_bytes"]
            and cloud_ref.checksum_sha256 == candidate_content.get("output_sha256")
            and cloud_ref.local_source_path_hash == expected_local_path_hash
            and cloud_ref.upload_status == "VERIFIED"
            and cloud_ref.verification_status == "CHECKSUM_VERIFIED"
            and cloud_ref.local_cleanup_status == "NOT_ELIGIBLE"
            and cloud_ref.uploaded_at is not None
            and cloud_ref.cleaned_at is None
            and cloud_ref.source_refs == source_refs
            and cloud_ref.retention_policy == expected_retention_policy
            and cloud_ref.technical_appendix == expected_cloud_appendix
        )
        lineage_content = lineage.content or {}
        required_lineage_keys = {
            "schema_version",
            "run_id",
            "project_id",
            "review_round",
            "output_sha256",
            "output_size_bytes",
            "review_media_candidate",
            "drive_archive_receipt",
            "drive_final_media_proof",
            "cloud_media_ref",
            "technical_media_qc",
            "human_full_watch_receipt",
            "drive_finalization_authority",
            "frozen_authority",
            "source_refs",
            "provider_key",
            "provider_type",
            "media_qc_report_id",
            "production_eligible",
            "human_pass_required_and_present",
            "publish_execution_authorized",
            "youtube_calls",
        }
        lineage_cloud = lineage_content.get("cloud_media_ref") or {}
        lineage_exact = bool(
            set(lineage_content) == required_lineage_keys
            and lineage_content.get("schema_version")
            == "mr1.final-media-lineage-receipt.v1"
            and lineage_content.get("run_id") == state["run_id"]
            and lineage_content.get("project_id") == state["project_id"]
            and lineage_content.get("review_round")
            == int(state.get("review_round") or 1)
            and lineage_content.get("output_sha256")
            == candidate_content.get("output_sha256")
            and output.is_file()
            and not output.is_symlink()
            and lineage_content.get("output_size_bytes") == output.stat().st_size
            and lineage_content.get("review_media_candidate")
            == {
                "artifact_version_id": str(candidate.id),
                "content_hash": candidate.content_hash,
            }
            and lineage_content.get("drive_archive_receipt")
            == {
                "artifact_version_id": str(drive.id),
                "content_hash": drive.content_hash,
                "archive_identity": state["archive_identity"],
            }
            and lineage_content.get("drive_final_media_proof") == final_drive_proof
            and lineage_content.get("technical_media_qc")
            == {
                "artifact_version_id": str(technical_qc.id),
                "content_hash": technical_qc.content_hash,
                "result": "PASS",
            }
            and lineage_content.get("human_full_watch_receipt")
            == {
                "artifact_version_id": str(human_receipt.id),
                "content_hash": human_receipt.content_hash,
                "decision": "PASS",
            }
            and lineage_content.get("drive_finalization_authority")
            == {
                "phase": deepcopy(
                    state["task_authorization"]["drive_idempotency_phases"][1]
                ),
                "distinct_from_canonical_archive": True,
                "verified_supplement_required_before_final_media_ref": True,
            }
            and lineage_content.get("frozen_authority") == frozen_lineage
            and lineage_content.get("source_refs") == source_refs
            and lineage_content.get("provider_key") == "mr1-native-ffmpeg-renderer"
            and lineage_content.get("provider_type") == "LOCAL_RENDERER_CAPABILITY"
            and lineage_content.get("media_qc_report_id") is None
            and lineage_content.get("production_eligible") is True
            and lineage_content.get("human_pass_required_and_present") is True
            and lineage_content.get("publish_execution_authorized") is False
            and lineage_content.get("youtube_calls") == 0
            and lineage_cloud.get("id") == str(cloud_ref.id)
            and lineage_cloud.get("drive_file_id") == cloud_ref.drive_file_id
            and lineage_cloud.get("drive_folder_id") == cloud_ref.drive_folder_id
            and lineage_cloud.get("web_view_link") == cloud_ref.web_view_link
            and lineage_cloud.get("file_name") == cloud_ref.file_name
            and lineage_cloud.get("mime_type") == cloud_ref.mime_type
            and lineage_cloud.get("size_bytes") == cloud_ref.size_bytes
            and lineage_cloud.get("checksum_sha256") == cloud_ref.checksum_sha256
            and lineage_cloud.get("upload_status") == "VERIFIED"
            and lineage_cloud.get("verification_status") == "CHECKSUM_VERIFIED"
            and lineage_cloud.get("source_refs_hash")
            == content_hash({"source_refs": source_refs})
        )
        refs = list(
            self.session.scalars(
                select(FinalMediaRef).where(
                    FinalMediaRef.video_project_id == uuid.UUID(state["project_id"]),
                    FinalMediaRef.lineage_artifact_version_id == lineage.id,
                )
            ).all()
        )
        expected_duration = (
            Decimal(str(candidate_content["duration_seconds"]))
            if candidate_content.get("duration_seconds") is not None
            else None
        )
        final_ref_exact = bool(
            len(refs) == 1
            and refs[0].id == final_ref.id
            and final_ref.company_id == project.company_id
            and final_ref.channel_workspace_id == project.channel_workspace_id
            and final_ref.video_project_id == project.id
            and final_ref.uploaded_video_id is None
            and final_ref.file_ref == str(output)
            and final_ref.media_type == "LONG_FORM_FINAL"
            and final_ref.duration_seconds == expected_duration
            and final_ref.provider_key == "mr1-native-ffmpeg-renderer"
            and final_ref.provider_type == "LOCAL_RENDERER_CAPABILITY"
            and final_ref.checksum_sha256 == candidate_content.get("output_sha256")
            and final_ref.media_qc_report_id is None
            and final_ref.cloud_media_ref_id == cloud_ref.id
            and final_ref.lineage_artifact_version_id == lineage.id
            and final_ref.aspect_ratio == "16:9"
            and final_ref.resolution == "1920x1080"
            and output.is_file()
            and not output.is_symlink()
            and _sha256_file(output) == final_ref.checksum_sha256
        )
        if not cloud_exact or not lineage_exact or not final_ref_exact:
            raise ValidationFailureError("MR1_FINAL_MEDIA_AUTHORITY_INVALID")
        return final_ref

    def _revalidate_final_archive_supplement(
        self,
        *,
        state: dict[str, Any],
        candidate: ArtifactVersion,
        drive: ArtifactVersion,
        human_receipt: ArtifactVersion,
        final_lineage: ArtifactVersion,
    ) -> ArtifactVersion:
        supplement_ref = state.get("final_archive_supplement") or {}
        try:
            supplement = self._exact_version(
                uuid.UUID(str(supplement_ref["artifact_version_id"])),
                str(supplement_ref["content_hash"]),
                FINAL_ARCHIVE_SUPPLEMENT_ARTIFACT_TYPE,
                uuid.UUID(state["project_id"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValidationFailureError(
                "MR1_FINALIZATION_SUPPLEMENT_REF_INVALID"
            ) from exc
        body = deepcopy(supplement.content or {})
        manifest = body.get("supplement_manifest") or {}
        remote = body.get("remote_verification_receipt") or {}
        files = manifest.get("files") or []
        task_authorization = state.get("task_authorization") or {}
        task_authorization_core = {
            key: value
            for key, value in task_authorization.items()
            if key != "content_hash"
        }
        phases = task_authorization.get("drive_idempotency_phases") or []
        finalization_ledger = (state.get("attempts") or {}).get(
            MR1_DRIVE_FINALIZATION_OPERATION_KEY
        )
        review_round = int(state.get("review_round") or 1)
        expected_idempotency_key = mr1_drive_finalization_idempotency_key(
            run_id=state["run_id"],
            review_round=review_round,
        )
        expected_idempotency_fingerprint = _idempotency_fingerprint(
            approval_content_hash=state["approval_content_hash"],
            run_id=state["run_id"],
            provider="google_drive",
            operation="finalization_supplement",
            scene_id=None,
        )
        expected_idempotency_identity = {
            "operation_key": MR1_DRIVE_FINALIZATION_OPERATION_KEY,
            "idempotency_key": expected_idempotency_key,
            "idempotency_fingerprint": expected_idempotency_fingerprint,
            "review_round": review_round,
            "distinct_from_canonical_archive": True,
            "automatic_retry_allowed": False,
        }
        try:
            attempt_artifact = self.session.get(
                Artifact,
                uuid.UUID(
                    str(
                        state["attempt_artifact_ids"][
                            MR1_DRIVE_FINALIZATION_OPERATION_KEY
                        ]
                    )
                ),
            )
            attempt_version = (
                self.session.get(
                    ArtifactVersion,
                    attempt_artifact.current_version_id,
                )
                if attempt_artifact is not None
                and attempt_artifact.current_version_id is not None
                else None
            )
        except (KeyError, TypeError, ValueError):
            attempt_artifact = None
            attempt_version = None
        ledger_without_self_ref = deepcopy(finalization_ledger or {})
        state_attempt_version_id = ledger_without_self_ref.pop(
            "artifact_version_id", None
        )
        persisted_ledger = deepcopy(
            (attempt_version.content or {}) if attempt_version is not None else {}
        )
        persisted_ledger.pop("artifact_version_id", None)
        ledger_exact = bool(
            isinstance(finalization_ledger, dict)
            and finalization_ledger.get("state") == "SUCCEEDED"
            and finalization_ledger.get("submit_state") == "SUCCEEDED"
            and finalization_ledger.get("attempt_count") == 1
            and finalization_ledger.get("network_submit_started") is True
            and finalization_ledger.get("automatic_retry_allowed") is False
            and finalization_ledger.get("review_round") == review_round
            and finalization_ledger.get("idempotency_key") == expected_idempotency_key
            and finalization_ledger.get("idempotency_fingerprint")
            == expected_idempotency_fingerprint
            and attempt_artifact is not None
            and attempt_artifact.artifact_type == ATTEMPT_ARTIFACT_TYPE
            and attempt_artifact.status == "approved"
            and attempt_version is not None
            and attempt_artifact.current_version_id == attempt_version.id
            and attempt_version.status == "approved"
            and state_attempt_version_id == str(attempt_version.id)
            and persisted_ledger == ledger_without_self_ref
            and attempt_version.content_hash
            == content_hash(attempt_version.content or {})
        )
        try:
            recomputed_remote_verification = self._validate_drive_receipt_proof(
                state=state,
                request_manifest=manifest,
                final_manifest=manifest,
                receipt=remote,
            )
        except (KeyError, RuntimeError, TypeError, ValueError):
            recomputed_remote_verification = None
        expected_bindings = {
            "review_media_candidate": {
                "artifact_version_id": str(candidate.id),
                "content_hash": candidate.content_hash,
            },
            "canonical_drive_archive_receipt": {
                "artifact_version_id": str(drive.id),
                "content_hash": drive.content_hash,
            },
            "human_full_watch_receipt": {
                "artifact_version_id": str(human_receipt.id),
                "content_hash": human_receipt.content_hash,
            },
            "final_media_lineage_receipt": {
                "artifact_version_id": str(final_lineage.id),
                "content_hash": final_lineage.content_hash,
            },
        }
        artifact_versions = {
            "MR1_HUMAN_FULL_WATCH_RECEIPT": (
                HUMAN_RECEIPT_ARTIFACT_TYPE,
                human_receipt,
            ),
            "MR1_FINAL_MEDIA_LINEAGE_RECEIPT": (
                FINAL_LINEAGE_ARTIFACT_TYPE,
                final_lineage,
            ),
        }
        files_exact = isinstance(files, list) and len(files) == 2
        if files_exact:
            for item in files:
                role = item.get("logical_role")
                authority = artifact_versions.get(role)
                path = Path(str(item.get("source_path") or ""))
                try:
                    wrapper = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    files_exact = False
                    break
                if not isinstance(wrapper, dict) or authority is None:
                    files_exact = False
                    break
                artifact_type, version = authority
                wrapper_core = {
                    key: value
                    for key, value in wrapper.items()
                    if key != "content_hash"
                }
                if (
                    path.is_symlink()
                    or not path.is_file()
                    or _sha256_file(path) != item.get("sha256")
                    or _md5_file(path) != item.get("md5")
                    or path.stat().st_size != item.get("size_bytes")
                    or wrapper.get("content_hash") != content_hash(wrapper_core)
                    or wrapper.get("artifact_type") != artifact_type
                    or wrapper.get("artifact_id") != str(version.artifact_id)
                    or wrapper.get("artifact_version_id") != str(version.id)
                    or wrapper.get("artifact_content_hash") != version.content_hash
                    or wrapper.get("content") != (version.content or {})
                ):
                    files_exact = False
                    break
        if (
            body.get("schema_version") != "mr1.drive-finalization-supplement-receipt.v1"
            or body.get("run_id") != state["run_id"]
            or body.get("project_id") != state["project_id"]
            or body.get("archive_identity") != state["archive_identity"]
            or body.get("review_round") != review_round
            or any(body.get(key) != value for key, value in expected_bindings.items())
            or any(
                manifest.get(key) != value for key, value in expected_bindings.items()
            )
            or manifest.get("schema_version")
            != "mr1.finalization-archive-supplement-manifest.v1"
            or manifest.get("run_id") != state["run_id"]
            or manifest.get("project_id") != state["project_id"]
            or manifest.get("archive_identity") != state["archive_identity"]
            or manifest.get("review_round") != review_round
            or task_authorization.get("content_hash")
            != content_hash(task_authorization_core)
            or len(phases) != 2
            or not ledger_exact
            or manifest.get("drive_phase_authority") != phases[1]
            or manifest.get("idempotency_identity") != expected_idempotency_identity
            or manifest.get("item_count") != 2
            or manifest.get("total_size_bytes")
            != sum(item.get("size_bytes") or 0 for item in files)
            or manifest.get("item_set_hash") != content_hash({"files": files})
            or not files_exact
            or remote.get("archive_phase") != "FINALIZATION_SUPPLEMENT"
            or remote.get("run_id") != state["run_id"]
            or remote.get("archive_identity") != state["archive_identity"]
            or remote.get("ARCHIVE_VERIFIED") is not True
            or remote.get("archive_state") != "VERIFIED"
            or remote.get("supplement_manifest_hash") != content_hash(manifest)
            or remote.get("supplement_item_set_hash") != manifest.get("item_set_hash")
            or remote.get("canonical_drive_archive_receipt")
            != expected_bindings["canonical_drive_archive_receipt"]
            or not self._drive_receipt_hash_valid(remote)
            or not self._drive_verification_exact(remote.get("verification"))
            or remote.get("verification") != recomputed_remote_verification
            or body.get("exact_supplement_item_set_verified") is not True
            or body.get("canonical_review_archive_mutated") is not False
            or body.get("final_media_registration_allowed") is not True
        ):
            raise ValidationFailureError(
                "MR1_FINALIZATION_SUPPLEMENT_AUTHORITY_INVALID"
            )
        return supplement

    def _find_run_by_approval(self, approval_id: uuid.UUID) -> ArtifactVersion | None:
        matches: list[ArtifactVersion] = []
        for artifact in self._artifacts(RUN_ARTIFACT_TYPE):
            if artifact.current_version_id is None:
                continue
            version = self.session.get(ArtifactVersion, artifact.current_version_id)
            if version is not None and (version.content or {}).get(
                "approval_id"
            ) == str(approval_id):
                matches.append(version)
        if len(matches) > 1:
            raise ValidationFailureError("MULTIPLE_MR1_RUNS_FOR_SINGLE_APPROVAL")
        return matches[0] if matches else None

    def _require_run(
        self, run_id: uuid.UUID, *, lock: bool = False
    ) -> tuple[Artifact, ArtifactVersion]:
        matches: list[tuple[Artifact, ArtifactVersion]] = []
        query = select(Artifact).where(Artifact.artifact_type == RUN_ARTIFACT_TYPE)
        if lock:
            query = query.with_for_update()
        for artifact in self.session.scalars(query).all():
            if artifact.current_version_id is None:
                continue
            version = self.session.get(ArtifactVersion, artifact.current_version_id)
            if version is not None and (version.content or {}).get("run_id") == str(
                run_id
            ):
                matches.append((artifact, version))
        if len(matches) != 1:
            raise NotFoundError(f"exact MR1 run not found: {run_id}")
        return matches[0]

    def _find_artifact_for_run(
        self, run_id: uuid.UUID, artifact_type: str
    ) -> ArtifactVersion | None:
        matches: list[ArtifactVersion] = []
        for artifact in self._artifacts(artifact_type):
            if artifact.current_version_id is None:
                continue
            version = self.session.get(ArtifactVersion, artifact.current_version_id)
            if version is not None and (version.content or {}).get("run_id") == str(
                run_id
            ):
                matches.append(version)
        if len(matches) > 1:
            raise ValidationFailureError(
                f"MULTIPLE_MR1_ARTIFACTS_FOR_RUN:{artifact_type}"
            )
        return matches[0] if matches else None

    def _find_human_receipt(self, run_id: uuid.UUID) -> ArtifactVersion | None:
        return self._find_artifact_for_run(run_id, HUMAN_RECEIPT_ARTIFACT_TYPE)

    def _artifacts(self, artifact_type: str) -> list[Artifact]:
        return list(
            self.session.scalars(
                select(Artifact).where(Artifact.artifact_type == artifact_type)
            ).all()
        )

    def _exact_version(
        self,
        version_id: uuid.UUID,
        expected_hash: str,
        artifact_type: str,
        project_id: uuid.UUID,
        *,
        allow_cross_project: bool = False,
        status_policy: str = "approved",
        fresh_lock: bool = False,
    ) -> ArtifactVersion:
        get_options = (
            {
                "populate_existing": True,
                "with_for_update": True,
            }
            if fresh_lock
            else {}
        )
        version = self.session.get(
            ArtifactVersion,
            version_id,
            **get_options,
        )
        artifact = (
            self.session.get(
                Artifact,
                version.artifact_id,
                **get_options,
            )
            if version is not None
            else None
        )
        if version is not None and (
            version.content_hash != expected_hash
            or content_hash(version.content or {}) != version.content_hash
        ):
            raise ValidationFailureError(f"MR1_{artifact_type.upper()}_HASH_MISMATCH")
        allowed_statuses = {
            "approved": ({"approved"}, {"approved"}),
            "approved_package": (
                {"approved"},
                {"submitted", "approved"},
            ),
            "package_authority": (
                {"in_review", "approved"},
                {"submitted", "approved"},
            ),
        }
        if status_policy not in allowed_statuses:
            raise ValidationFailureError(
                f"MR1_ARTIFACT_STATUS_POLICY_INVALID:{status_policy}"
            )
        artifact_statuses, version_statuses = allowed_statuses[status_policy]
        if (
            version is None
            or artifact is None
            or artifact.artifact_type != artifact_type
            or artifact.current_version_id != version.id
            or artifact.status not in artifact_statuses
            or version.status not in version_statuses
            or (not allow_cross_project and artifact.video_project_id != project_id)
        ):
            raise ValidationFailureError(
                f"MR1_EXACT_ARTIFACT_VERSION_REQUIRED:{artifact_type}"
            )
        return version

    def _validate_existing_run_command(
        self, state: dict[str, Any], command: MR1StartCommand
    ) -> None:
        if (
            state.get("approval_id") != str(command.approval_id)
            or state.get("approval_content_hash") != command.approval_content_hash
            or state.get("project_id") != str(command.project_id)
            or state.get("package_artifact_version_id")
            != str(command.package_artifact_version_id)
        ):
            raise ValidationFailureError("MR1_EXISTING_RUN_COMMAND_CONFLICT")

    def _revalidate_waiting_or_final_boundary(
        self, state: dict[str, Any], *, final: bool
    ) -> None:
        candidate_ref = state.get("review_media_candidate") or {}
        drive_ref = state.get("drive_archive") or {}
        try:
            project_id = uuid.UUID(str(state["project_id"]))
            candidate = self._exact_version(
                uuid.UUID(str(candidate_ref["artifact_version_id"])),
                str(candidate_ref["content_hash"]),
                CANDIDATE_ARTIFACT_TYPE,
                project_id,
            )
            drive = self._exact_version(
                uuid.UUID(str(drive_ref["artifact_version_id"])),
                str(drive_ref["content_hash"]),
                DRIVE_RECEIPT_ARTIFACT_TYPE,
                project_id,
            )
            assigned_actor = uuid.UUID(
                str(state["final_human_review_assigned_to_user_id"])
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValidationFailureError(
                "MR1_WAITING_BOUNDARY_CURRENT_REFS_INVALID"
            ) from exc
        candidate_content = deepcopy(candidate.content or {})
        drive_content = deepcopy(drive.content or {})
        self._validate_candidate_payload(state, candidate_content)
        self._revalidate_canonical_drive_receipt(
            state=state,
            drive_content=drive_content,
        )
        technical_qc = self._require_technical_qc_authority(
            state=state,
            candidate=candidate,
            candidate_content=candidate_content,
        )
        review_round = int(state.get("review_round") or 1)
        local_files_exact = True
        items = drive_content.get("items") or []
        if not isinstance(items, list) or not items:
            local_files_exact = False
        else:
            for item in items:
                if not isinstance(item, dict):
                    local_files_exact = False
                    break
                path = Path(str(item.get("source_path") or ""))
                if (
                    not path.is_file()
                    or path.is_symlink()
                    or _sha256_file(path) != item.get("sha256")
                    or path.stat().st_size != item.get("size_bytes")
                ):
                    local_files_exact = False
                    break
        if (
            content_hash(drive_content) != drive.content_hash
            or drive_content.get("run_id") != state["run_id"]
            or drive_content.get("archive_identity") != state["archive_identity"]
            or drive_content.get("review_round") != review_round
            or drive_content.get("review_media_candidate_artifact_version_id")
            != str(candidate.id)
            or drive_content.get("review_media_candidate_content_hash")
            != candidate.content_hash
            or drive_content.get("output_sha256")
            != candidate_content.get("output_sha256")
            or drive_content.get("status") != "VERIFIED"
            or drive_content.get("archive_state") != "VERIFIED"
            or drive_content.get("ARCHIVE_VERIFIED") is not True
            or not self._drive_receipt_hash_valid(drive_content)
            or not self._drive_verification_exact(drive_content.get("verification"))
            or not local_files_exact
        ):
            raise ValidationFailureError("MR1_WAITING_BOUNDARY_DRIVE_RECEIPT_INVALID")
        task = self._require_final_human_review_task(
            state=state,
            candidate=candidate,
            drive=drive,
            decided_by_user_id=assigned_actor,
            allow_completed=final,
        )
        if final:
            try:
                receipt = self._exact_version(
                    uuid.UUID(str(state["human_review_receipt_artifact_version_id"])),
                    str(state["human_review_receipt_content_hash"]),
                    HUMAN_RECEIPT_ARTIFACT_TYPE,
                    project_id,
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise ValidationFailureError(
                    "MR1_FINAL_BOUNDARY_HUMAN_RECEIPT_INVALID"
                ) from exc
            body = receipt.content or {}
            receipt_technical = body.get("technical_media_qc") or {}
            if (
                body.get("decision") != "PASS"
                or body.get("review_round") != review_round
                or body.get("reviewed_output_sha256")
                != candidate_content.get("output_sha256")
                or (body.get("final_human_review_task") or {}).get("review_task_id")
                != str(task.id)
                or task.status != "completed"
                or state.get("human_review") != "PASS"
                or not state.get("final_media_ref_id")
                or receipt_technical.get("artifact_version_id") != str(technical_qc.id)
                or receipt_technical.get("content_hash") != technical_qc.content_hash
            ):
                raise ValidationFailureError(
                    "MR1_FINAL_BOUNDARY_HUMAN_AUTHORITY_INVALID"
                )
            self._require_existing_final_media_authority(
                state=state,
                candidate=candidate,
                candidate_content=candidate_content,
                drive=drive,
                drive_content=drive_content,
                technical_qc=technical_qc,
                human_receipt=receipt,
            )

    def _public_result(
        self, run_version: ArtifactVersion, state: dict[str, Any]
    ) -> dict[str, Any]:
        waiting = state.get("current_state") in {
            "AWAITING_HUMAN_FULL_WATCH",
            "WAITING_HUMAN_REVIEW",
        }
        final = state.get("current_state") == "FINAL_MEDIA_REGISTERED"
        if waiting or final:
            self._revalidate_waiting_or_final_boundary(state, final=final)
        blocked = str(state.get("current_state") or "").startswith("BLOCKED")
        local = state.get("local_result") or {}
        creative = (
            local.get("creative_media_qc_result")
            or (state.get("review_media_candidate") or {}).get("creative_review_result")
            or "FAIL"
        )
        if waiting and creative == "PASS":
            creative = "REVIEW_REQUIRED"
        active_attempt_keys = state.get("active_provider_attempt_keys") or {}
        pexels_scenes = self._state_pexels_scenes(state)
        required_attempt_keys = [
            "elevenlabs:narration",
            "elevenlabs:forced_alignment",
            *[
                active_attempt_keys.get(f"pexels:{scene}", f"pexels:{scene}")
                for scene in pexels_scenes
            ],
        ]
        providers_complete = all(
            state.get("attempts", {}).get(key, {}).get("state") == "SUCCEEDED"
            for key in required_attempt_keys
        )
        required_provider_execution_pass = (
            self._required_provider_execution_pass(
                state=state,
                pexels_scenes=pexels_scenes,
                required_attempt_keys=required_attempt_keys,
            )
        )
        asset_resolution_pass = self._asset_resolution_pass(
            state=state,
            local=local,
        )
        attempts = []
        for key, item in (state.get("attempts") or {}).items():
            if key == "google_drive:archive":
                continue
            public_attempt = deepcopy(item)
            public_attempt["scene_id"] = item.get("scene_id")
            attempts.append(public_attempt)
        attempts.sort(
            key=lambda item: (
                {"elevenlabs": 1, "forced_alignment": 2, "pexels_api": 3}.get(
                    item["provider"], 99
                ),
                item.get("scene_id") or "",
            )
        )
        scene_executions = sorted(
            deepcopy(list((state.get("scene_executions") or {}).values())),
            key=lambda item: item["scene_id"],
        )
        internal_counts = state.get("provider_call_counts") or {}
        reuse_manifest = state.get("reuse_decision_manifest") or {}
        expected_reuse_count = int(reuse_manifest.get("prior_output_reuse_count") or 0)
        reuse_decisions_pass = bool(
            reuse_manifest.get("fail_closed") is True
            and state.get("prior_output_reuse_count") == expected_reuse_count
            and set((state.get("reuse_materialization_receipts") or {}))
            == set(reuse_manifest.get("reuse_allowed_output_keys") or [])
        )
        drive_receipt = state.get("drive_archive") or {}
        final_supplement_bound = bool(
            (state.get("final_archive_supplement") or {}).get("artifact_version_id")
            and (state.get("final_archive_supplement") or {}).get("content_hash")
        )
        archive_verified = bool(
            drive_receipt.get("archive_state") == "VERIFIED"
            and drive_receipt.get("ARCHIVE_VERIFIED") is True
            and not drive_receipt.get("mismatch_reason_codes")
            and drive_receipt.get("artifact_version_id")
            and drive_receipt.get("content_hash")
            and self._drive_receipt_hash_valid(drive_receipt)
            and (not final or final_supplement_bound)
        )
        public_counts = {
            "elevenlabs_narration": int(internal_counts.get("elevenlabs") or 0),
            "forced_alignment": int(internal_counts.get("forced_alignment") or 0),
            "pexels_scene_flows": int(internal_counts.get("pexels") or 0),
            "google_gemini_image": 0,
            "google_veo": 0,
            "google_drive_archive_flows": int(internal_counts.get("drive") or 0),
            "youtube": 0,
        }
        current_state = state["current_state"]
        mr1_final = (
            "PASS"
            if final
            else "WAITING_HUMAN_REVIEW"
            if waiting
            else "REPAIRABLE_PRE_SUBMIT_FAILURE"
            if current_state == "BLOCKED_PRE_SUBMIT_REPAIRABLE"
            else current_state
            if blocked
            else "IN_PROGRESS"
        )
        result = {
            "run_id": state["run_id"],
            "run_artifact_version_id": str(run_version.id),
            "approval_id": state["approval_id"],
            "approval_content_hash": state["approval_content_hash"],
            "exact_target": {
                "project_id": state["project_id"],
                "package_artifact_version_id": state["package_artifact_version_id"],
                "package_content_hash": state.get("package_content_hash"),
                **deepcopy(
                    (state.get("master_preflight") or {}).get("exact_target") or {}
                ),
            },
            "current_state": current_state,
            "attempts": attempts,
            "provider_call_counts": public_counts,
            "reused_output_count": int(state.get("prior_output_reuse_count") or 0),
            "reuse_materialization_receipts": deepcopy(
                state.get("reuse_materialization_receipts") or {}
            ),
            "scene_executions": scene_executions,
            "event_order": list(state.get("event_order") or []),
            "review_media_candidate": deepcopy(state.get("review_media_candidate")),
            "drive_archive": deepcopy(state.get("drive_archive")),
            "technical_media_qc": deepcopy(state.get("technical_media_qc")),
            "monthly_budget_reservation": deepcopy(
                state.get("monthly_budget_reservation")
            ),
            "final_media_ref_id": state.get("final_media_ref_id"),
            "final_cloud_media_ref_id": state.get("final_cloud_media_ref_id"),
            "final_media_lineage": deepcopy(state.get("final_media_lineage")),
            "workspace": state.get("workspace"),
            "archive_identity": state.get("archive_identity"),
            "render_identity": state.get("render_identity"),
            "narration": deepcopy(
                (state.get("provider_outputs") or {}).get("narration")
            ),
            "alignment": deepcopy(
                (state.get("provider_outputs") or {}).get("alignment")
            ),
            "canonical_timeline": deepcopy(
                (state.get("local_result") or {}).get("canonical_timeline")
                or state.get("temporal_authority")
            ),
            "failed_stage": (state.get("local_result") or {}).get("failed_stage"),
            "MR1_ENTRY": "PASS",
            "MR1_APPROVAL_BINDING": "PASS",
            "MR1_REUSE_DECISIONS": (
                "PASS" if reuse_decisions_pass else "NOT_APPLICABLE"
            ),
            "MR1_PREFLIGHT": "PASS",
            "MR1_REQUIRED_PROVIDER_EXECUTION": (
                "PASS" if required_provider_execution_pass else "FAIL"
            ),
            "MR1_ELEVENLABS": "PASS"
            if final
            or state.get("attempts", {}).get("elevenlabs:narration", {}).get("state")
            == "SUCCEEDED"
            else "FAIL",
            "MR1_FORCED_ALIGNMENT": "PASS"
            if final
            or state.get("attempts", {})
            .get("elevenlabs:forced_alignment", {})
            .get("state")
            == "SUCCEEDED"
            else "FAIL",
            "MR1_CANONICAL_TIMELINE": "PASS"
            if waiting or final or state.get("temporal_authority")
            else "FAIL",
            "MR1_PEXELS": "PASS" if final or providers_complete else "FAIL",
            "MR1_GEMINI_IMAGE": "NOT_REQUIRED",
            "MR1_GOOGLE_VEO": "NOT_REQUIRED",
            "MR1_NATIVE_ASSETS": "PASS" if waiting or final else "FAIL",
            "MR1_ASSET_RESOLUTION": "PASS" if asset_resolution_pass else "FAIL",
            "MR1_MEDIA_NORMALIZATION": "PASS" if waiting or final else "FAIL",
            "MR1_NATIVE_RENDER_PLAN": "PASS" if waiting or final else "FAIL",
            "MR1_NATIVE_MOTION_COMPILER": "PASS" if waiting or final else "FAIL",
            "MR1_NATIVE_FFMPEG_RENDER": "PASS" if waiting or final else "FAIL",
            "MR1_TECHNICAL_MEDIA_QC": "PASS" if waiting or final else "FAIL",
            "MR1_CREATIVE_MEDIA_QC": "PASS"
            if final
            else creative
            if waiting
            else "FAIL",
            "MR1_REVIEW_MEDIA_CANDIDATE": "PASS" if waiting or final else "FAIL",
            "MR1_DRIVE_ARCHIVE": "PASS" if archive_verified else "FAIL",
            "ARCHIVE_VERIFIED": archive_verified,
            "MR1_PROVIDER_CALL_COUNT": int(
                (state.get("provider_call_counts") or {}).get("logical_total") or 0
            ),
            "MR1_RENDER_ATTEMPTS": int(state.get("render_attempts") or 0),
            "MR1_REPAIR_CYCLES": len(state.get("repair_cycles") or []),
            "MR1_HUMAN_REVIEW": "PASS"
            if final
            else "PENDING"
            if waiting
            else state.get("human_review", "PENDING"),
            "MR1_FINAL_MEDIA_REF": "PASS" if final else "NOT_CREATED",
            "MR1_FINAL": "PASS"
            if final
            else "WAITING_HUMAN_REVIEW"
            if waiting
            else mr1_final,
            "DESTINATION_STATUS": "PENDING_PLATFORM_ID",
            "UPLOAD_READY": False,
            "PUBLISH_EXECUTION_READY": False,
            "PROCEED_TO_DESTINATION_CLOSEOUT": final,
            "PROCEED_TO_PUB1": False,
            "youtube_calls": 0,
        }
        if state.get("human_review_receipt_artifact_version_id"):
            result["human_review_receipt_artifact_version_id"] = state[
                "human_review_receipt_artifact_version_id"
            ]
            result["human_review_receipt_content_hash"] = state.get(
                "human_review_receipt_content_hash"
            )
        if state.get("final_archive_supplement"):
            result["final_archive_supplement"] = deepcopy(
                state["final_archive_supplement"]
            )
        return result

    @staticmethod
    def _required_provider_execution_pass(
        *,
        state: dict[str, Any],
        pexels_scenes: tuple[str, ...],
        required_attempt_keys: list[str],
    ) -> bool:
        """Prove the exact approved media-provider work completed.

        A successful ledger alone is insufficient: the corresponding durable
        output and exact Pexels scene route must also exist. Approved immutable
        reuse remains valid because its seeded ledger is SUCCEEDED without a
        current-run submit and carries the original durable output.
        """

        attempts = state.get("attempts") or {}
        outputs = state.get("provider_outputs") or {}
        routes = state.get("approved_visual_routes") or {}
        task_authorization = state.get("task_authorization") or {}
        counts = state.get("provider_call_counts") or {}
        expected_outputs = {
            "narration",
            "alignment",
            *(f"pexels:{scene_id}" for scene_id in pexels_scenes),
        }
        if (
            set(outputs) != expected_outputs
            or not isinstance(routes, dict)
            or set(routes) != set(ALL_SCENES)
            or tuple(
                scene_id
                for scene_id in ALL_SCENES
                if routes.get(scene_id) == "PEXELS_VIDEO"
            )
            != pexels_scenes
            or task_authorization.get("provider_substitution_allowed") is not False
            or task_authorization.get("automatic_retry_allowed") is not False
            or task_authorization.get("youtube_upload_authorized") is not False
            or any(
                int(counts.get(key) or 0) != 0
                for key in ("gemini_image", "google_veo", "youtube")
            )
        ):
            return False

        expected_attempts = {
            required_attempt_keys[0]: ("elevenlabs", "narration", "narration"),
            required_attempt_keys[1]: (
                "forced_alignment",
                "forced_alignment",
                "alignment",
            ),
            **{
                attempt_key: (
                    "pexels_api",
                    "supporting_asset_acquisition",
                    f"pexels:{scene_id}",
                )
                for attempt_key, scene_id in zip(
                    required_attempt_keys[2:],
                    pexels_scenes,
                    strict=True,
                )
            },
        }
        for attempt_key, (provider, operation, output_key) in expected_attempts.items():
            ledger = attempts.get(attempt_key) or {}
            output = outputs.get(output_key) or {}
            if (
                ledger.get("state") != "SUCCEEDED"
                or ledger.get("provider") != provider
                or ledger.get("operation") != operation
                or ledger.get("approval_id") != state.get("approval_id")
                or ledger.get("approval_content_hash")
                != state.get("approval_content_hash")
                or ledger.get("provider_substitution_allowed") is not False
                or ledger.get("automatic_retry_allowed") is not False
                or not isinstance(output, dict)
                or not output
            ):
                return False
            reused = ledger.get("immutable_output_reused") is True
            if reused:
                if (
                    ledger.get("submit_state")
                    != "REUSED_IMMUTABLE_OUTPUT_NO_SUBMIT"
                    or ledger.get("attempt_count") != 0
                    or ledger.get("network_submit_started") is not False
                    or not ledger.get("reuse_receipt")
                ):
                    return False
            elif (
                ledger.get("submit_state") != "SUCCEEDED"
                or ledger.get("attempt_count") != 1
                or ledger.get("network_submit_started") is not True
                or not _is_sha256(str(ledger.get("request_hash") or ""))
            ):
                return False

        scene_executions = state.get("scene_executions") or {}
        for scene_id in pexels_scenes:
            execution = scene_executions.get(scene_id) or {}
            output = outputs.get(f"pexels:{scene_id}") or {}
            if (
                routes.get(scene_id) != "PEXELS_VIDEO"
                or execution.get("scene_id") != scene_id
                or execution.get("route") != "PEXELS_VIDEO"
                or execution.get("provider") != "pexels_api"
                or execution.get("status") != "PASS"
                or execution.get("fallback_used") is not False
                or output.get("scene_id") != scene_id
                or output.get("route") != "PEXELS_VIDEO"
                or output.get("provider") != "pexels_api"
                or output.get("provider_call_made") is not True
                or output.get("automatic_fallback_used") is True
                or output.get("provider_substitution_used") is True
            ):
                return False
        return True

    def _asset_resolution_pass(
        self,
        *,
        state: dict[str, Any],
        local: dict[str, Any],
    ) -> bool:
        """Reopen exact nine-scene provenance before claiming resolution PASS."""

        candidate = state.get("review_media_candidate") or {}
        normalization = local.get("media_normalization") or {}
        routes = state.get("approved_visual_routes") or {}
        if (
            local.get("state") != "READY_FOR_ARCHIVE"
            or normalization.get("result") != "PASS"
            or normalization.get("actual_bytes_probed") is not True
            or candidate.get("provenance_complete") is not True
            or candidate.get("rights_disclosure_resolved") is not True
            or not candidate.get("asset_provenance_manifest_ref")
            or not candidate.get("asset_provenance_manifest_hash")
            or not isinstance(routes, dict)
            or set(routes) != set(ALL_SCENES)
        ):
            return False
        try:
            self._revalidate_candidate_authority_bindings(
                state=state,
                candidate=candidate,
            )
            provenance_path = Path(candidate["asset_provenance_manifest_ref"])
            provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
        except (
            ValidationFailureError,
            OSError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ):
            return False
        items = provenance.get("items") if isinstance(provenance, dict) else None
        if not isinstance(items, list) or len(items) != len(ALL_SCENES):
            return False
        by_scene = {
            item.get("scene_id"): item for item in items if isinstance(item, dict)
        }
        return bool(
            set(by_scene) == set(ALL_SCENES)
            and all(
                by_scene[scene_id].get("route") == routes[scene_id]
                and by_scene[scene_id].get("fallback_used") is False
                and by_scene[scene_id].get("source_sha256")
                and by_scene[scene_id].get("normalized_sha256")
                for scene_id in ALL_SCENES
            )
        )

    def _workspace(self, run_id: uuid.UUID) -> Path:
        root = self.workspace_root.resolve()
        root.mkdir(parents=True, exist_ok=True)
        path = (root / str(run_id)).resolve()
        if root != path and root not in path.parents:
            raise ValidationFailureError("MR1_WORKSPACE_CONTAINMENT_FAILED")
        return path

    def _durable_boundary(self) -> None:
        self.session.flush()
        if self.commit_boundaries:
            self.session.commit()


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if hasattr(value, "__dict__") and not isinstance(value, type):
        return {key: _jsonable(item) for key, item in vars(value).items()}
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (uuid.UUID, Path, datetime, Decimal)):
        return str(value)
    return value


def _redact_volatile(value: Any) -> Any:
    forbidden = {
        "api_key",
        "access_token",
        "authorization",
        "headers",
        "download_url",
        "volatile_download_url",
        "signed_url",
    }
    if isinstance(value, dict):
        return {
            key: _redact_volatile(item)
            for key, item in value.items()
            if key.lower() not in forbidden
        }
    if isinstance(value, list):
        return [_redact_volatile(item) for item in value]
    return value


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_sha256(value: str) -> bool:
    return bool(
        len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _md5_file(path: Path) -> str:
    digest = hashlib.md5(usedforsecurity=False)
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_json_file_payload(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        indent=2,
        sort_keys=True,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    try:
        part.write_text(
            json.dumps(payload, indent=2, sort_keys=True, default=str),
            encoding="utf-8",
        )
        os.replace(part, path)
    finally:
        part.unlink(missing_ok=True)
