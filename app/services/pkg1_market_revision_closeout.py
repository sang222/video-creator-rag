from __future__ import annotations

import uuid
from copy import deepcopy
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contracts.channel_policy import ChannelScopedPolicy
from app.contracts.pkg1_market_revision_closeout import (
    PKG1MarketRevisionApprovalCommand,
)
from app.contracts.workflow import (
    ApprovalDecisionCreate,
    ArtifactCreate,
    ArtifactVersionCreate,
)
from app.core.errors import NotFoundError, ValidationFailureError
from app.core.time import utc_now
from app.db.models import (
    ApprovalDecision,
    Artifact,
    ArtifactVersion,
    ChannelProfileVersion,
    ChannelWorkspace,
    CompiledChannelPolicySnapshot,
    ContentCategory,
    EditorialCalendarSlot,
    ReviewTask,
    VideoProject,
)
from app.services.config_registry import content_hash
from app.services.nich1 import NicheContractDigestCompiler
from app.services.pkg1_market_revision import (
    LPRO1_ORCHESTRATOR_VERSION,
    LPRO1_RENDER_CONTRACT_VERSION,
    PROJECT_TYPE,
    REQUIRED_HISTORICAL_ARTIFACT_TYPES,
    REUSED_ARTIFACT_TYPES,
    REVISION_SCHEMA_VERSION,
    PKG1MarketRevisionService,
)
from app.services.workflow import ApprovalService, ArtifactService, ReviewService


APPROVAL_SCOPE = "PKG1_MARKET_REVISION_PACKAGE_PLANNING"
RECEIPT_ARTIFACT_TYPE = "pkg1_market_revision_human_review_receipt"
READ_MODEL_ARTIFACT_TYPE = "pkg1_market_revision_read_model"

MANDATORY_REVIEWED_TYPES = {
    "script",
    "voice_policy",
    "visual_direction_contract",
    "visual_plan",
    "visual_source_decision_set",
    "thumbnail_brief",
    "publishing_metadata_package",
    "target_market_profile",
    "target_market_digest",
    "market_alignment_dossier",
    "destination_binding",
    "provider_execution_plan",
    "cost_estimate_snapshot",
    "rights_disclosure_completeness_report",
    "synthetic_media_disclosure_receipt_draft",
    "asset_provenance_plan",
    "publish_risk_dossier",
    "publish_handoff_package",
}


class PKG1MarketRevisionCloseoutService:
    """Apply an explicit operator PASS without authorizing MR1 or publishing."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.revision_service = PKG1MarketRevisionService(session)

    def closeout(
        self,
        command: PKG1MarketRevisionApprovalCommand,
    ) -> dict[str, Any]:
        project = self.session.scalar(
            select(VideoProject)
            .where(VideoProject.id == command.project_id)
            .with_for_update()
        )
        if project is None:
            raise NotFoundError(f"PKG1 market revision not found: {command.project_id}")
        if project.project_type != PROJECT_TYPE:
            raise ValidationFailureError("CLOSEOUT_TARGET_IS_NOT_PKG1_MARKET_REVISION")
        if project.status == "approved":
            return self._read_existing_closeout(project=project, command=command)
        if project.status != "in_review":
            raise ValidationFailureError("PKG1_MARKET_REVISION_NOT_PENDING_REVIEW")

        pending_project_ids = set(
            self.session.scalars(
                select(VideoProject.id)
                .join(
                    ReviewTask,
                    ReviewTask.video_project_id == VideoProject.id,
                )
                .where(
                    VideoProject.project_type == PROJECT_TYPE,
                    VideoProject.status == "in_review",
                    ReviewTask.review_type == "final_human",
                    ReviewTask.status.in_(["open", "in_progress"]),
                )
            ).all()
        )
        if pending_project_ids != {project.id}:
            raise ValidationFailureError(
                "EXACTLY_ONE_CANONICAL_PENDING_MARKET_REVISION_REQUIRED"
            )

        review = self.session.get(ReviewTask, command.review_task_id)
        if review is None:
            raise NotFoundError(f"review task not found: {command.review_task_id}")
        if (
            review.video_project_id != project.id
            or review.review_type != "final_human"
            or review.status not in {"open", "in_progress"}
            or review.target_artifact_version_id
            != command.reviewed_package_artifact_version_id
            or review.target_id != command.reviewed_package_artifact_version_id
        ):
            raise ValidationFailureError("EXACT_PENDING_REVIEW_TARGET_MISMATCH")
        if review.assigned_to_user_id != command.decided_by_user_id:
            raise ValidationFailureError("OPERATOR_IS_NOT_ASSIGNED_EXACT_REVIEWER")

        version_ids = self._project_artifact_version_ids(project.id)
        existing_approvals = (
            list(
                self.session.scalars(
                    select(ApprovalDecision).where(
                        ApprovalDecision.target_artifact_version_id.in_(version_ids)
                    )
                ).all()
            )
            if version_ids
            else []
        )
        if existing_approvals:
            raise ValidationFailureError("PENDING_REVISION_ALREADY_HAS_APPROVAL")

        evidence = self._revalidate_exact_hashes(project=project, command=command)
        historical_project: VideoProject = evidence["historical_project"]
        historical_before = self.revision_service._historical_fingerprint(
            historical_project,
            self.revision_service._current_artifacts(historical_project.id),
        )
        no_execution_before = self.revision_service._no_execution_counts()
        decision_timestamp = utc_now()

        approval = ApprovalService(self.session).create_approval_decision(
            data=ApprovalDecisionCreate(
                target_type="artifact_version",
                target_id=command.reviewed_package_artifact_version_id,
                target_artifact_version_id=(
                    command.reviewed_package_artifact_version_id
                ),
                decision="approved",
                decided_by_user_id=command.decided_by_user_id,
                rationale=(
                    "Explicit operator PASS for the exact PKG1 market revision "
                    "package and production-planning authority."
                ),
                metadata={
                    "approval_ref": command.approval_ref,
                    "approval_scope": command.approval_scope,
                    "decision_source": command.decision_source,
                    "review_authority": command.review_authority,
                    "operator_decision_text": command.operator_decision_text,
                    "review_task_id": str(review.id),
                    "revision_id": str(command.reviewed_revision_id),
                    "revision_version": command.reviewed_revision_version,
                    "revision_hash": command.reviewed_revision_hash,
                    "package_artifact_version_id": str(
                        command.reviewed_package_artifact_version_id
                    ),
                    "package_content_hash": command.reviewed_package_hash,
                    "reviewed_snapshot_hash": evidence[
                        "reviewed_snapshot_hash"
                    ],
                    "production_package_approved": True,
                    "mr1_reapproval_preparation_allowed": True,
                    "mr1_execution_authorized": False,
                    "publish_execution_authorized": False,
                },
                decision_basis={
                    "PKG1_MARKET_REVISION_HUMAN_REVIEW": "PASS",
                    "PKG1_MARKET_REVISION_FINAL": "PASS",
                    "PRODUCTION_PACKAGE_APPROVED": True,
                    "MR1_REAPPROVAL_ENTRY": "READY",
                    "MR1_EXECUTION": "NOT_STARTED",
                    "PROCEED_TO_MR1_REAPPROVAL": True,
                    "PROCEED_TO_MR1": False,
                },
                evidence_basis={
                    "reviewed_snapshot_hash": evidence[
                        "reviewed_snapshot_hash"
                    ],
                    "reviewed_artifacts": evidence["reviewed_artifacts"],
                    "exact_bindings": evidence["exact_bindings"],
                    "operator_decision_text": command.operator_decision_text,
                    "review_task_id": str(review.id),
                },
                policy_basis={
                    "channel_profile_version": evidence["exact_bindings"][
                        "channel_profile_version"
                    ],
                    "compiled_channel_policy_snapshot": evidence[
                        "exact_bindings"
                    ]["compiled_channel_policy_snapshot"],
                    "target_market_profile": evidence["exact_bindings"][
                        "target_market_profile"
                    ],
                    "target_market_digest": evidence["exact_bindings"][
                        "target_market_digest"
                    ],
                    "destination_binding": evidence["exact_bindings"][
                        "destination_binding"
                    ],
                },
                context_pack_ref=(
                    f"artifact-version://"
                    f"{command.reviewed_package_artifact_version_id}"
                ),
                human_decision_note=(
                    "Operator supplied literal PASS. Codex persisted the receipt; "
                    "it did not perform or originate the human review."
                ),
            ),
            assigned_final_review_task_id=review.id,
            correlation_id="pkg1-market-revision-human-closeout-approval",
        )

        receipt_content = self._receipt_content(
            command=command,
            review=review,
            approval=approval,
            decision_timestamp=decision_timestamp,
            evidence=evidence,
        )
        receipt_artifact, receipt_version = self._create_closeout_artifact(
            project=project,
            artifact_type=RECEIPT_ARTIFACT_TYPE,
            content=receipt_content,
            created_by_user_id=command.decided_by_user_id,
            evidence_refs=[
                {
                    "type": "approval_decision",
                    "id": str(approval.id),
                    "ref": command.approval_ref,
                }
            ],
            context_refs=[
                {
                    "type": "reviewed_package",
                    "artifact_version_id": str(
                        command.reviewed_package_artifact_version_id
                    ),
                    "content_hash": command.reviewed_package_hash,
                }
            ],
        )

        ReviewService(self.session).complete_review_task(
            review_task_id=review.id,
            actor_user_id=command.decided_by_user_id,
            resolution_ref=command.approval_ref,
            approval_decision_ids=[approval.id],
            correlation_id="pkg1-market-revision-human-closeout-review",
        )

        readiness_content = self._readiness_content(
            command=command,
            approval=approval,
            receipt_version=receipt_version,
            evidence=evidence,
        )
        readiness_artifact, readiness_version = self._create_closeout_artifact(
            project=project,
            artifact_type="mr1_readiness_state",
            content=readiness_content,
            created_by_user_id=command.decided_by_user_id,
            evidence_refs=[
                {
                    "type": "human_review_receipt",
                    "artifact_version_id": str(receipt_version.id),
                    "content_hash": receipt_version.content_hash,
                },
                {"type": "approval_decision", "id": str(approval.id)},
            ],
            context_refs=[
                {
                    "type": "reviewed_package",
                    "artifact_version_id": str(
                        command.reviewed_package_artifact_version_id
                    ),
                    "content_hash": command.reviewed_package_hash,
                }
            ],
        )

        read_model_content = self._read_model_content(
            command=command,
            approval=approval,
            receipt_version=receipt_version,
            readiness_version=readiness_version,
            evidence=evidence,
        )
        read_model_artifact, read_model_version = self._create_closeout_artifact(
            project=project,
            artifact_type=READ_MODEL_ARTIFACT_TYPE,
            content=read_model_content,
            created_by_user_id=command.decided_by_user_id,
            evidence_refs=[
                {"type": "approval_decision", "id": str(approval.id)},
                {
                    "type": "human_review_receipt",
                    "artifact_version_id": str(receipt_version.id),
                    "content_hash": receipt_version.content_hash,
                },
            ],
            context_refs=[
                {
                    "type": "mr1_reapproval_readiness",
                    "artifact_version_id": str(readiness_version.id),
                    "content_hash": readiness_version.content_hash,
                }
            ],
        )

        package_artifact: Artifact = evidence["package_artifact"]
        package_artifact.status = "approved"
        receipt_artifact.status = "approved"
        readiness_artifact.status = "approved"
        read_model_artifact.status = "approved"
        project.status = "approved"
        audience_summary = deepcopy(project.audience_delivery_summary or {})
        audience_summary.update(
            {
                "production_package_status": "APPROVED",
                "market_alignment": "PASS",
                "profile_version": 3,
                "market": "US",
                "destination_handle": "@SmallTeamAI",
                "destination_verification": "PENDING_PLATFORM_ID",
                "publish_readiness": "NOT_READY",
                "upload_ready": False,
                "mr1_reapproval": "READY",
                "mr1_execution": "NOT_STARTED",
            }
        )
        project.audience_delivery_summary = audience_summary
        self.session.flush()

        if self.revision_service._no_execution_counts() != no_execution_before:
            raise ValidationFailureError("CLOSEOUT_EXECUTION_BOUNDARY_CHANGED")
        if self.revision_service._historical_fingerprint(
            historical_project,
            self.revision_service._current_artifacts(historical_project.id),
        ) != historical_before:
            raise ValidationFailureError("HISTORICAL_PKG1_MUTATED_DURING_CLOSEOUT")

        result = self.read_closeout(project.id)
        result["no_execution_counts_before"] = no_execution_before
        result["no_execution_counts_after"] = (
            self.revision_service._no_execution_counts()
        )
        return result

    def read_closeout(self, project_id: uuid.UUID) -> dict[str, Any]:
        project = self.session.get(VideoProject, project_id)
        if project is None or project.project_type != PROJECT_TYPE:
            raise NotFoundError(f"PKG1 market revision not found: {project_id}")
        artifacts = self.revision_service._current_artifacts(project.id)
        required = {
            "package_manifest",
            RECEIPT_ARTIFACT_TYPE,
            "mr1_readiness_state",
            READ_MODEL_ARTIFACT_TYPE,
        }
        missing = required - set(artifacts)
        if missing:
            raise ValidationFailureError(
                "PKG1_MARKET_REVISION_CLOSEOUT_ARTIFACTS_MISSING:"
                + ",".join(sorted(missing))
            )
        package = artifacts["package_manifest"]
        receipt = artifacts[RECEIPT_ARTIFACT_TYPE]
        readiness = artifacts["mr1_readiness_state"]
        read_model = artifacts[READ_MODEL_ARTIFACT_TYPE]
        approvals = list(
            self.session.scalars(
                select(ApprovalDecision).where(
                    ApprovalDecision.target_artifact_version_id == package.id,
                    ApprovalDecision.decision == "approved",
                )
            ).all()
        )
        approvals = [
            item
            for item in approvals
            if (item.metadata_ or {}).get("approval_scope") == APPROVAL_SCOPE
        ]
        if len(approvals) != 1:
            raise ValidationFailureError(
                "EXACT_MARKET_REVISION_PACKAGE_APPROVAL_REQUIRED"
            )
        approval = approvals[0]
        review_id = uuid.UUID(receipt.content["review_task_id"])
        review = self.session.get(ReviewTask, review_id)
        if (
            review is None
            or review.status != "completed"
            or review.target_artifact_version_id != package.id
            or receipt.content.get("approval_decision_id") != str(approval.id)
            or receipt.content.get("receipt_content_authority")
            != "ARTIFACT_VERSION_CONTENT_HASH"
            or receipt.content.get("decision") != "PASS"
            or receipt.content.get("decision_source") != "OPERATOR"
            or receipt.content.get("review_authority") != "HUMAN"
        ):
            raise ValidationFailureError("CLOSEOUT_RECEIPT_OR_REVIEW_INVALID")
        if content_hash(receipt.content) != receipt.content_hash:
            raise ValidationFailureError("CLOSEOUT_RECEIPT_HASH_MISMATCH")
        if content_hash(readiness.content) != readiness.content_hash:
            raise ValidationFailureError("MR1_REAPPROVAL_READINESS_HASH_MISMATCH")
        if content_hash(read_model.content) != read_model.content_hash:
            raise ValidationFailureError("PKG1_REVISION_READ_MODEL_HASH_MISMATCH")

        return {
            "video_project_id": str(project.id),
            "project_status": project.status,
            "revision_id": receipt.content["revision"]["revision_id"],
            "revision_version": receipt.content["revision"]["revision_version"],
            "revision_hash": receipt.content["revision"]["revision_hash"],
            "package_artifact_version_id": str(package.id),
            "package_content_hash": package.content_hash,
            "review_task_id": str(review.id),
            "review_task_status": review.status,
            "approval_decision_id": str(approval.id),
            "approval_ref": (approval.metadata_ or {}).get("approval_ref"),
            "approval_scope": (approval.metadata_ or {}).get("approval_scope"),
            "human_review_receipt_artifact_id": str(receipt.artifact_id),
            "human_review_receipt_artifact_version_id": str(receipt.id),
            "human_review_receipt_content_hash": receipt.content_hash,
            "mr1_readiness_artifact_id": str(readiness.artifact_id),
            "mr1_readiness_artifact_version_id": str(readiness.id),
            "mr1_readiness_content_hash": readiness.content_hash,
            "read_model_artifact_id": str(read_model.artifact_id),
            "read_model_artifact_version_id": str(read_model.id),
            "read_model_content_hash": read_model.content_hash,
            "PKG1_MARKET_REVISION_HUMAN_REVIEW": "PASS",
            "PKG1_MARKET_REVISION_FINAL": "PASS",
            "revision_status": "APPROVED",
            "PRODUCTION_PACKAGE_APPROVED": True,
            "FINAL_MARKET_PACKAGE_PENDING_MEDIA": True,
            "UPLOAD_READY": False,
            "PUBLISH_EXECUTION_READY": False,
            "destination_status": "PENDING_PLATFORM_ID",
            "publish_blocker": "PENDING_PLATFORM_ID",
            "publish_blocker_reason_code": (
                "DESTINATION_PLATFORM_ID_NOT_VERIFIED"
            ),
            "MR1_REAPPROVAL_ENTRY": "READY",
            "MR1_EXECUTION": "NOT_STARTED",
            "MR1_PROVIDER_CALL_COUNT": 0,
            "MR1_RENDER_STATUS": "NOT_STARTED",
            "MR1_HUMAN_REVIEW": "PENDING",
            "PROCEED_TO_MR1_REAPPROVAL": True,
            "PROCEED_TO_MR1": False,
            "provider_calls": 0,
            "render_calls": 0,
            "drive_calls": 0,
            "youtube_calls": 0,
        }

    def _read_existing_closeout(
        self,
        *,
        project: VideoProject,
        command: PKG1MarketRevisionApprovalCommand,
    ) -> dict[str, Any]:
        evidence = self._revalidate_exact_hashes(project=project, command=command)
        result = self.read_closeout(project.id)
        if (
            result["approval_ref"] != command.approval_ref
            or result["revision_hash"] != command.reviewed_revision_hash
            or result["package_artifact_version_id"]
            != str(command.reviewed_package_artifact_version_id)
            or result["package_content_hash"] != command.reviewed_package_hash
        ):
            raise ValidationFailureError("EXISTING_CLOSEOUT_TARGET_CONFLICT")
        approval = self.session.get(
            ApprovalDecision, uuid.UUID(result["approval_decision_id"])
        )
        if (
            approval is None
            or approval.decided_by_user_id != command.decided_by_user_id
            or (approval.metadata_ or {}).get("reviewed_snapshot_hash")
            != evidence["reviewed_snapshot_hash"]
        ):
            raise ValidationFailureError("EXISTING_CLOSEOUT_AUTHORITY_CONFLICT")
        return result

    def _revalidate_exact_hashes(
        self,
        *,
        project: VideoProject,
        command: PKG1MarketRevisionApprovalCommand,
    ) -> dict[str, Any]:
        artifacts = self.revision_service._current_artifacts(project.id)
        package = artifacts.get("package_manifest")
        if package is None:
            raise ValidationFailureError("REVIEWED_PACKAGE_MANIFEST_MISSING")
        package_artifact = self.session.get(Artifact, package.artifact_id)
        if (
            package.id != command.reviewed_package_artifact_version_id
            or package.content_hash != command.reviewed_package_hash
            or content_hash(package.content) != command.reviewed_package_hash
            or package_artifact is None
            or package_artifact.current_version_id != package.id
        ):
            raise ValidationFailureError("REVIEWED_PACKAGE_HASH_MISMATCH_NEW_REVISION_REQUIRED")
        manifest = package.content or {}
        if (
            manifest.get("revision_id") != str(command.reviewed_revision_id)
            or manifest.get("revision_version") != command.reviewed_revision_version
            or manifest.get("revision_hash") != command.reviewed_revision_hash
        ):
            raise ValidationFailureError("REVIEWED_REVISION_IDENTITY_MISMATCH")

        reviewed: dict[str, dict[str, Any]] = {
            "package_manifest": {
                **self.revision_service._version_ref(package),
                "lineage_role": "REVIEWED_PACKAGE",
            }
        }
        for manifest_key, lineage_role in (
            ("reused_artifacts", "REUSED_UNCHANGED"),
            ("revised_artifacts", "REVISED_OR_REBUILT"),
        ):
            members = manifest.get(manifest_key) or {}
            if not isinstance(members, dict):
                raise ValidationFailureError(
                    f"REVIEWED_PACKAGE_{manifest_key.upper()}_INVALID"
                )
            for artifact_type, expected in members.items():
                version = self.session.get(
                    ArtifactVersion,
                    uuid.UUID(expected["artifact_version_id"]),
                )
                artifact = (
                    self.session.get(Artifact, version.artifact_id)
                    if version is not None
                    else None
                )
                if (
                    version is None
                    or artifact is None
                    or str(artifact.id) != expected["artifact_id"]
                    or version.version_number != expected["version_number"]
                    or version.content_hash != expected["content_hash"]
                    or content_hash(version.content) != version.content_hash
                    or artifact.current_version_id != version.id
                ):
                    raise ValidationFailureError(
                        f"REVIEWED_ARTIFACT_HASH_MISMATCH_NEW_REVISION_REQUIRED:{artifact_type}"
                    )
                if (
                    manifest_key == "revised_artifacts"
                    and artifact.video_project_id != project.id
                ):
                    raise ValidationFailureError(
                        f"REVISED_ARTIFACT_PROJECT_MISMATCH:{artifact_type}"
                    )
                reviewed[artifact_type] = {
                    **self.revision_service._version_ref(version),
                    "lineage_role": lineage_role,
                }

        if not MANDATORY_REVIEWED_TYPES.issubset(reviewed):
            raise ValidationFailureError(
                "MANDATORY_REVIEWED_ARTIFACTS_MISSING:"
                + ",".join(sorted(MANDATORY_REVIEWED_TYPES - set(reviewed)))
            )
        revised = manifest.get("revised_artifacts") or {}
        recomputed_output_hash = content_hash(
            {
                key: value["content_hash"]
                for key, value in sorted(revised.items())
            }
        )
        if recomputed_output_hash != manifest.get("planning_output_set_hash"):
            raise ValidationFailureError("PLANNING_OUTPUT_SET_HASH_MISMATCH")

        bindings = manifest.get("exact_bindings") or {}
        profile_binding = bindings.get("channel_profile_version") or {}
        snapshot_binding = bindings.get("compiled_channel_policy_snapshot") or {}
        profile = self.session.get(
            ChannelProfileVersion, uuid.UUID(profile_binding["id"])
        )
        snapshot = self.session.get(
            CompiledChannelPolicySnapshot, uuid.UUID(snapshot_binding["id"])
        )
        if (
            profile is None
            or snapshot is None
            or profile.profile_input_hash != profile_binding.get("content_hash")
            or content_hash(profile.profile_input) != profile.profile_input_hash
            or snapshot.content_hash != snapshot_binding.get("content_hash")
            or content_hash(snapshot.compiled_payload) != snapshot.content_hash
            or snapshot.channel_profile_version_id != profile.id
            or project.policy_snapshot_id != snapshot.id
            or project.channel_profile_version_id != profile.id
        ):
            raise ValidationFailureError("PROFILE_OR_SNAPSHOT_HASH_REVALIDATION_FAILED")
        policy = ChannelScopedPolicy.model_validate(
            (snapshot.compiled_payload or {}).get("channel_scoped_policy")
        )
        market_profile = policy.target_market_profile
        market_digest = policy.target_market_digest
        destination_policy = policy.destination_binding_policy
        if (
            market_profile is None
            or market_digest is None
            or destination_policy is None
        ):
            raise ValidationFailureError("FROZEN_MARKET_AUTHORITY_MISSING")
        destination = destination_policy.destination
        if (
            market_profile.content_hash
            != (bindings.get("target_market_profile") or {}).get("content_hash")
            or market_digest.content_hash
            != (bindings.get("target_market_digest") or {}).get("content_hash")
            or destination.content_hash
            != (bindings.get("destination_binding") or {}).get("content_hash")
            or destination.destination_status != "PENDING_PLATFORM_ID"
            or destination.platform_channel_id is not None
            or destination.credential_ref is not None
        ):
            raise ValidationFailureError("MARKET_OR_DESTINATION_HASH_REVALIDATION_FAILED")

        category_binding = bindings.get("content_category") or {}
        category = self.session.get(
            ContentCategory, uuid.UUID(category_binding["id"])
        )
        slot = self.session.get(
            EditorialCalendarSlot,
            self._uuid_from_ref(
                (bindings.get("editorial_slot") or {}).get("ref"),
                "editorial-slot://",
            ),
        )
        historical_project = self.session.get(
            VideoProject,
            self._uuid_from_ref(
                (bindings.get("historical_video_project") or {}).get("ref"),
                "video-project://",
            ),
        )
        channel = self.session.get(ChannelWorkspace, project.channel_workspace_id)
        if (
            category is None
            or slot is None
            or historical_project is None
            or channel is None
        ):
            raise ValidationFailureError("REVISION_LINEAGE_TARGET_MISSING")
        if (
            category.content_hash != category_binding.get("content_hash")
            or slot.category_id != category.id
            or slot.policy_snapshot_id != snapshot.id
            or self.revision_service._project_hash(project)
            != (bindings.get("revision_video_project") or {}).get("content_hash")
            or self.revision_service._project_hash(historical_project)
            != (bindings.get("historical_video_project") or {}).get("content_hash")
        ):
            raise ValidationFailureError("REVISION_LINEAGE_HASH_MISMATCH")

        historical_artifacts = self.revision_service._current_artifacts(
            historical_project.id
        )
        missing_historical = REQUIRED_HISTORICAL_ARTIFACT_TYPES - set(
            historical_artifacts
        )
        historical_package = historical_artifacts.get("package_manifest")
        supersedes = manifest.get("supersedes") or {}
        if (
            missing_historical
            or historical_package is None
            or str(historical_package.id)
            != supersedes.get("artifact_version_id")
            or historical_package.content_hash != supersedes.get("content_hash")
        ):
            raise ValidationFailureError("HISTORICAL_PKG1_LINEAGE_CHANGED")

        series_plan = (profile.profile_input or {}).get("series_plan") or []
        series_key = str((series_plan[0] if series_plan else {}).get("key") or "")
        channel_contract_hash = content_hash(
            (snapshot.compiled_payload or {}).get("channel_contract_json") or {}
        )
        semantic_seed = {
            "schema_version": REVISION_SCHEMA_VERSION,
            "builder_version": "pkg1-market-revision-builder/1.0.0",
            "historical_package_ref": f"artifact-version://{historical_package.id}",
            "historical_package_hash": historical_package.content_hash,
            "profile_v3_ref": f"channel-profile-version://{profile.id}",
            "profile_v3_version": profile.version,
            "profile_v3_hash": profile.profile_input_hash,
            "snapshot_v3_ref": f"compiled-policy-snapshot://{snapshot.id}",
            "snapshot_v3_version": snapshot.snapshot_version,
            "snapshot_v3_hash": snapshot.content_hash,
            "channel_contract_hash": channel_contract_hash,
            "target_market_profile_hash": market_profile.content_hash,
            "target_market_digest_hash": market_digest.content_hash,
            "destination_binding_hash": destination.content_hash,
            "category_hash": category.content_hash,
            "series_key": series_key,
            "content_pillar": category.content_pillar,
            "reused_artifact_hashes": {
                key: historical_artifacts[key].content_hash
                for key in REUSED_ARTIFACT_TYPES
            },
            "revision_source_artifact_hashes": {
                key: historical_artifacts[key].content_hash
                for key in sorted(REQUIRED_HISTORICAL_ARTIFACT_TYPES)
                if key != "package_manifest"
            },
        }
        recomputed_revision_id = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                "vcos:pkg1-market-revision:" + content_hash(semantic_seed),
            )
        )
        expected_slot_id = uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"vcos:pkg1-market-revision:{recomputed_revision_id}:editorial-slot",
        )
        niche_digest = NicheContractDigestCompiler().compile(
            channel=channel,
            profile_version=profile,
            policy_snapshot=snapshot,
            category=category,
            editorial_slot=slot,
        )
        recomputed_revision_hash = content_hash(
            {
                **semantic_seed,
                "editorial_slot_ref": niche_digest.editorial_slot_ref,
                "editorial_slot_hash": niche_digest.editorial_slot_hash,
                "niche_contract_digest_hash": niche_digest.content_hash,
                "lpro1_orchestrator_version": LPRO1_ORCHESTRATOR_VERSION,
                "lpro1_render_contract_version": LPRO1_RENDER_CONTRACT_VERSION,
            }
        )
        niche_binding = bindings.get("niche_contract_digest") or {}
        if (
            recomputed_revision_id != str(command.reviewed_revision_id)
            or recomputed_revision_hash != command.reviewed_revision_hash
            or slot.id != expected_slot_id
            or niche_binding.get("governance_hash") != niche_digest.content_hash
        ):
            raise ValidationFailureError(
                "REVISION_HASH_REVALIDATION_FAILED_NEW_REVISION_REQUIRED"
            )

        reviewed_snapshot_hash = content_hash(
            {
                "revision_id": recomputed_revision_id,
                "revision_version": command.reviewed_revision_version,
                "revision_hash": recomputed_revision_hash,
                "package_artifact_version_id": str(package.id),
                "package_content_hash": package.content_hash,
                "planning_output_set_hash": recomputed_output_hash,
                "exact_bindings": bindings,
                "reviewed_artifacts": reviewed,
            }
        )
        return {
            "package": package,
            "package_artifact": package_artifact,
            "exact_bindings": deepcopy(bindings),
            "reviewed_artifacts": reviewed,
            "reviewed_snapshot_hash": reviewed_snapshot_hash,
            "planning_output_set_hash": recomputed_output_hash,
            "historical_project": historical_project,
            "historical_package": historical_package,
            "market_profile": market_profile.model_dump(mode="json"),
            "market_digest": market_digest.model_dump(mode="json"),
            "destination": destination.model_dump(mode="json"),
        }

    @staticmethod
    def _receipt_content(
        *,
        command: PKG1MarketRevisionApprovalCommand,
        review: ReviewTask,
        approval: ApprovalDecision,
        decision_timestamp: Any,
        evidence: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "schema_version": "pkg1.market-revision-human-review-receipt.v1",
            "receipt_content_authority": "ARTIFACT_VERSION_CONTENT_HASH",
            "decision": command.decision,
            "decision_source": command.decision_source,
            "review_authority": command.review_authority,
            "operator_decision_text": command.operator_decision_text,
            "decision_timestamp": approval.decided_at.isoformat(),
            "command_received_timestamp": decision_timestamp.isoformat(),
            "decided_by_user_id": str(command.decided_by_user_id),
            "review_notes": command.review_notes,
            "approval_ref": command.approval_ref,
            "approval_scope": command.approval_scope,
            "approval_decision_id": str(approval.id),
            "review_task_id": str(review.id),
            "revision": {
                "video_project_id": str(command.project_id),
                "revision_id": str(command.reviewed_revision_id),
                "revision_version": command.reviewed_revision_version,
                "revision_hash": command.reviewed_revision_hash,
            },
            "reviewed_package": {
                "artifact_version_id": str(
                    command.reviewed_package_artifact_version_id
                ),
                "artifact_version_ref": (
                    f"artifact-version://"
                    f"{command.reviewed_package_artifact_version_id}"
                ),
                "content_hash": command.reviewed_package_hash,
                "planning_output_set_hash": evidence[
                    "planning_output_set_hash"
                ],
            },
            "reviewed_snapshot_hash": evidence["reviewed_snapshot_hash"],
            "exact_bindings": evidence["exact_bindings"],
            "reviewed_artifacts": evidence["reviewed_artifacts"],
            "historical_lineage": {
                "historical_pkg1_project_id": str(
                    evidence["historical_project"].id
                ),
                "historical_pkg1_package": PKG1MarketRevisionService._version_ref(
                    evidence["historical_package"]
                ),
                "old_mr1_approval": "SUPERSEDED_BY_PKG1_MARKET_REVISION",
                "old_mr1_approval_decision_id": evidence["package"].content[
                    "old_mr1_approval"
                ]["approval_decision_id"],
                "historical_evidence_mutated": False,
            },
            "approved_authority": {
                "production_package_planning": True,
                "mr1_reapproval_preparation": True,
            },
            "not_authorized": [
                "PROVIDER_EXECUTION",
                "MR1_EXECUTION",
                "PRODUCTION_RENDER",
                "DRIVE_ARCHIVE",
                "UPLOAD",
                "YOUTUBE_PUBLISH",
                "DESTINATION_VERIFICATION",
            ],
            "state_transition": {
                "PKG1_MARKET_REVISION_HUMAN_REVIEW": "PASS",
                "PKG1_MARKET_REVISION_FINAL": "PASS",
                "PRODUCTION_PACKAGE_APPROVED": True,
                "FINAL_MARKET_PACKAGE_PENDING_MEDIA": True,
                "UPLOAD_READY": False,
                "PUBLISH_EXECUTION_READY": False,
                "destination_status": "PENDING_PLATFORM_ID",
            },
        }

    @staticmethod
    def _readiness_content(
        *,
        command: PKG1MarketRevisionApprovalCommand,
        approval: ApprovalDecision,
        receipt_version: ArtifactVersion,
        evidence: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "schema_version": "pkg1.market-revision-mr1-reapproval-readiness.v1",
            "approved_revision": {
                "video_project_id": str(command.project_id),
                "revision_id": str(command.reviewed_revision_id),
                "revision_version": command.reviewed_revision_version,
                "revision_hash": command.reviewed_revision_hash,
            },
            "approved_package": {
                "artifact_version_id": str(
                    command.reviewed_package_artifact_version_id
                ),
                "content_hash": command.reviewed_package_hash,
                "approval_decision_id": str(approval.id),
                "approval_ref": command.approval_ref,
            },
            "human_review_receipt": PKG1MarketRevisionService._version_ref(
                receipt_version
            ),
            "reviewed_snapshot_hash": evidence["reviewed_snapshot_hash"],
            "exact_bindings": evidence["exact_bindings"],
            "required_binding_availability": {
                "approved_pkg1_market_revision": True,
                "profile_v3": True,
                "compiled_snapshot_v3": True,
                "target_market_profile": True,
                "market_alignment_dossier": True,
                "destination_binding": True,
                "provider_execution_plan": True,
                "cost_estimate_snapshot": True,
                "rights_disclosure_plan": True,
                "lpro1_production_execution_contract": True,
            },
            "old_mr1_approval": {
                **evidence["package"].content["old_mr1_approval"],
                "state_for_revision": "SUPERSEDED_BY_PKG1_MARKET_REVISION",
                "reuse_allowed": False,
            },
            "destination_status": "PENDING_PLATFORM_ID",
            "publish_blocker": "PENDING_PLATFORM_ID",
            "publish_blocker_reason_code": (
                "DESTINATION_PLATFORM_ID_NOT_VERIFIED"
            ),
            "MR1_REAPPROVAL_ENTRY": "READY",
            "MR1_EXECUTION": "NOT_STARTED",
            "MR1_PROVIDER_CALL_COUNT": 0,
            "MR1_RENDER_STATUS": "NOT_STARTED",
            "MR1_HUMAN_REVIEW": "PENDING",
            "PROCEED_TO_MR1_REAPPROVAL": True,
            "PROCEED_TO_MR1": False,
        }

    @staticmethod
    def _read_model_content(
        *,
        command: PKG1MarketRevisionApprovalCommand,
        approval: ApprovalDecision,
        receipt_version: ArtifactVersion,
        readiness_version: ArtifactVersion,
        evidence: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "schema_version": "pkg1.market-revision-read-model.v1",
            "video_project_id": str(command.project_id),
            "revision_id": str(command.reviewed_revision_id),
            "revision_hash": command.reviewed_revision_hash,
            "package_artifact_version_id": str(
                command.reviewed_package_artifact_version_id
            ),
            "package_content_hash": command.reviewed_package_hash,
            "approval_decision_id": str(approval.id),
            "approval_ref": command.approval_ref,
            "human_review_receipt": PKG1MarketRevisionService._version_ref(
                receipt_version
            ),
            "mr1_reapproval_readiness": PKG1MarketRevisionService._version_ref(
                readiness_version
            ),
            "reviewed_snapshot_hash": evidence["reviewed_snapshot_hash"],
            "PKG1_MARKET_REVISION_HUMAN_REVIEW": "PASS",
            "PKG1_MARKET_REVISION_FINAL": "PASS",
            "revision_status": "APPROVED",
            "PRODUCTION_PACKAGE_APPROVED": True,
            "market_alignment": "PASS",
            "profile": "v3",
            "market": "US",
            "destination": "@SmallTeamAI",
            "destination_verification": "PENDING_PLATFORM_ID",
            "publish_readiness": "NOT_READY",
            "UPLOAD_READY": False,
            "PUBLISH_EXECUTION_READY": False,
            "MR1_REAPPROVAL_ENTRY": "READY",
            "MR1_EXECUTION": "NOT_STARTED",
            "PROCEED_TO_MR1_REAPPROVAL": True,
            "PROCEED_TO_MR1": False,
            "operator_summary": {
                "package": "Production package approved",
                "destination": "Publish destination not yet fully verified",
                "publish": "Not ready for upload or publish execution",
                "mr1": "MR1 re-approval preparation is ready; execution is not started",
            },
        }

    def _create_closeout_artifact(
        self,
        *,
        project: VideoProject,
        artifact_type: str,
        content: dict[str, Any],
        created_by_user_id: uuid.UUID,
        evidence_refs: list[dict[str, Any]],
        context_refs: list[dict[str, Any]],
    ) -> tuple[Artifact, ArtifactVersion]:
        existing = list(
            self.session.scalars(
                select(Artifact).where(
                    Artifact.video_project_id == project.id,
                    Artifact.artifact_type == artifact_type,
                )
            ).all()
        )
        if existing:
            raise ValidationFailureError(
                f"UNEXPECTED_EXISTING_CLOSEOUT_ARTIFACT:{artifact_type}"
            )
        service = ArtifactService(self.session)
        artifact = service.create_artifact(
            data=ArtifactCreate(
                video_project_id=project.id,
                artifact_type=artifact_type,
                status="approved",
                created_by_user_id=created_by_user_id,
            ),
            correlation_id=f"pkg1-market-closeout-{artifact_type}",
        )
        version = service.create_artifact_version(
            data=ArtifactVersionCreate(
                artifact_id=artifact.id,
                content=deepcopy(content),
                status="approved",
                created_by_user_id=created_by_user_id,
                evidence_refs=deepcopy(evidence_refs),
                context_refs=deepcopy(context_refs),
                packaging_metadata={
                    "pkg1_market_revision_closeout": True,
                    "operator_decision": "PASS",
                    "provider_execution": "DISABLED",
                    "publish_execution": "DISABLED",
                },
            ),
            correlation_id=f"pkg1-market-closeout-version-{artifact_type}",
        )
        return artifact, version

    def _project_artifact_version_ids(
        self, project_id: uuid.UUID
    ) -> list[uuid.UUID]:
        return list(
            self.session.scalars(
                select(ArtifactVersion.id)
                .join(Artifact, ArtifactVersion.artifact_id == Artifact.id)
                .where(Artifact.video_project_id == project_id)
            ).all()
        )

    @staticmethod
    def _uuid_from_ref(value: Any, prefix: str) -> uuid.UUID:
        if not isinstance(value, str) or not value.startswith(prefix):
            raise ValidationFailureError(f"INVALID_EXACT_REF:{prefix}")
        try:
            return uuid.UUID(value.removeprefix(prefix))
        except ValueError as exc:
            raise ValidationFailureError(f"INVALID_EXACT_REF_UUID:{prefix}") from exc
