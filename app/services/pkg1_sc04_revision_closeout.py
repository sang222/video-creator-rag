from __future__ import annotations

import uuid
from copy import deepcopy
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contracts.geo_delivery import GeoMarketDeliveryCloseoutEvidence
from app.contracts.geo_delivery import StrictMarketLineageEnvelope
from app.contracts.pkg1_sc04_revision_closeout import (
    PKG1SC04RevisionApprovalCommand,
)
from app.contracts.workflow import (
    ApprovalDecisionCreate,
    ArtifactCreate,
    ArtifactVersionCreate,
)
from app.core.errors import NotFoundError, ValidationFailureError
from app.db.models import (
    ApprovalDecision,
    Artifact,
    ArtifactVersion,
    ReviewTask,
    VideoProject,
)
from app.services.config_registry import content_hash
from app.services.geo_delivery import StrictMarketLineageService
from app.services.pkg1_sc04_revision import (
    PROJECT_TYPE,
    PKG1SC04RevisionService,
)
from app.services.workflow import ApprovalService, ArtifactService, ReviewService


APPROVAL_SCOPE = "PKG1_SC04_REVISION_PACKAGE_PLANNING"
RECEIPT_ARTIFACT_TYPE = "pkg1_sc04_revision_human_review_receipt"
RECEIPT_SCHEMA_VERSION = "pkg1.sc04-human-review-receipt.v1"
HUMAN_RECEIPT_ARTIFACT_STATUS = "approved"
HUMAN_RECEIPT_VERSION_STATUS = "submitted"

_SUPERSEDED_MR1_SCOPES = {
    "MR1_PAID_EXECUTION",
    "MR1_REAL_PRODUCTION_EXECUTION",
    "MR1_EXACT_PROVIDER_ATTEMPT_CONTINUATION",
}


class PKG1SC04RevisionCloseoutService:
    """Persist an explicit human PASS; never authorize or start execution."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.revision_service = PKG1SC04RevisionService(session)

    def closeout(self, command: PKG1SC04RevisionApprovalCommand) -> dict[str, Any]:
        project = self.session.scalar(
            select(VideoProject)
            .where(VideoProject.id == command.project_id)
            .with_for_update()
        )
        if project is None:
            raise NotFoundError(f"PKG1 SC-04 revision not found: {command.project_id}")
        if project.project_type != PROJECT_TYPE:
            raise ValidationFailureError("CLOSEOUT_TARGET_IS_NOT_PKG1_SC04_REVISION")
        if project.status == "approved":
            result = self.read_closeout(project.id)
            self._validate_existing_command(command=command, result=result)
            return result
        if project.status != "in_review":
            raise ValidationFailureError("PKG1_SC04_REVISION_NOT_PENDING_REVIEW")

        review = self.session.get(ReviewTask, command.review_task_id)
        if review is None:
            raise NotFoundError(f"review task not found: {command.review_task_id}")
        if (
            review.video_project_id != project.id
            or review.review_type != "final_human"
            or review.status not in {"open", "in_progress"}
            or review.target_id != command.reviewed_package_artifact_version_id
            or review.target_artifact_version_id
            != command.reviewed_package_artifact_version_id
            or review.assigned_to_user_id != command.decided_by_user_id
        ):
            raise ValidationFailureError("EXACT_SC04_PENDING_REVIEW_TARGET_MISMATCH")

        evidence = self._revalidate_exact_package(project=project, command=command)
        package: ArtifactVersion = evidence["package"]
        package_artifact: Artifact = evidence["package_artifact"]
        existing_approvals = self._sc04_package_approvals(package.id)
        if existing_approvals:
            raise ValidationFailureError("PENDING_SC04_REVISION_ALREADY_HAS_APPROVAL")

        no_execution_before = (
            self.revision_service.source_service._no_execution_counts()
        )
        superseded_mr1 = self._superseded_mr1_approvals(
            source_package=evidence["source_package"],
            manifest=evidence["manifest"],
        )
        exact_bindings = evidence["manifest"]["exact_bindings"]
        target_market = exact_bindings["target_market_profile"]
        composite_alignment = evidence["composite_alignment"]
        geo_closeout: GeoMarketDeliveryCloseoutEvidence = evidence["geo_closeout"]
        approval = ApprovalService(self.session).create_approval_decision(
            data=ApprovalDecisionCreate(
                target_type="artifact_version",
                target_id=package.id,
                target_artifact_version_id=package.id,
                decision="approved",
                decided_by_user_id=command.decided_by_user_id,
                rationale=(
                    "Explicit human PASS for the exact immutable PKG1 SC-04 "
                    "revision package. Execution remains separately blocked."
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
                    "package_artifact_version_id": str(package.id),
                    "package_content_hash": package.content_hash,
                    "approved_package_hash": package.content_hash,
                    "effective_market_policy_hash": evidence["manifest"][
                        "effective_market_policy_hash"
                    ],
                    "destination_binding_id": str(
                        geo_closeout.destination_runtime.destination_binding_id
                    ),
                    "destination_binding_fingerprint": (
                        geo_closeout.destination_runtime.binding_fingerprint
                    ),
                    "target_market_profile_ref": target_market["ref"],
                    "target_market_profile_hash": target_market["content_hash"],
                    "market_alignment_dossier_ref": composite_alignment["ref"],
                    "market_alignment_dossier_hash": composite_alignment[
                        "content_hash"
                    ],
                    "approved_publish_timezone": evidence["approved_publish_timezone"],
                    "approved_publish_window": deepcopy(
                        evidence["approved_publish_window"]
                    ),
                    "production_package_approved": True,
                    "mr1_reapproval_preparation_allowed": True,
                    "mr1_execution_authorized": False,
                    "provider_execution_authorized": False,
                    "publish_execution_authorized": False,
                },
                decision_basis={
                    "PKG1_SC04_REVISION_HUMAN_REVIEW": "PASS",
                    "PKG1_SC04_REVISION_FINAL": "PASS",
                    "PRODUCTION_PACKAGE_APPROVED": True,
                    "MR1_EXECUTION": ("BLOCKED_REQUIRES_FRESH_SC04_PACKAGE_REAPPROVAL"),
                    "PROCEED_TO_MR1_REAPPROVAL": True,
                    "PROCEED_TO_MR1": False,
                },
                evidence_basis={
                    "reviewed_package": self.revision_service._version_ref(package),
                    "effective_artifacts": deepcopy(
                        evidence["manifest"]["effective_artifacts"]
                    ),
                    "effective_artifact_authority": deepcopy(
                        evidence["manifest"]["effective_artifact_authority"]
                    ),
                    "exact_bindings": deepcopy(exact_bindings),
                    "superseded_mr1_approvals": deepcopy(superseded_mr1),
                },
                policy_basis={
                    "compiled_channel_policy_snapshot": deepcopy(
                        exact_bindings["compiled_channel_policy_snapshot"]
                    ),
                    "target_market_profile": deepcopy(target_market),
                    "destination_binding": {
                        "id": str(
                            geo_closeout.destination_runtime.destination_binding_id
                        ),
                        "content_hash": (
                            geo_closeout.destination_runtime.binding_fingerprint
                        ),
                        "canonical_binding": deepcopy(
                            exact_bindings["destination_binding"]
                        ),
                    },
                    "market_alignment_dossier": {
                        "ref": composite_alignment["ref"],
                        "content_hash": composite_alignment["content_hash"],
                    },
                    "effective_ads_only_policy": deepcopy(
                        evidence["manifest"]["effective_monetization_policy"]
                    ),
                    "geo_closeout_evidence": deepcopy(
                        evidence["manifest"]["geo_market_delivery_closeout_evidence"]
                    ),
                    "effective_market_policy_hash": evidence["manifest"][
                        "effective_market_policy_hash"
                    ],
                    "composite_market_alignment_authority": deepcopy(
                        composite_alignment
                    ),
                    "provider_execution_authorized": False,
                    "publish_execution_authorized": False,
                },
                context_pack_ref=f"artifact-version://{package.id}",
                human_decision_note=(
                    "The operator supplied literal PASS. Codex persisted the "
                    "decision and did not originate the human review."
                ),
                policy_snapshot_id=project.policy_snapshot_id,
                destination_binding_id=(
                    geo_closeout.destination_runtime.destination_binding_id
                ),
                destination_binding_fingerprint=(
                    geo_closeout.destination_runtime.binding_fingerprint
                ),
                market_policy_hash=evidence["manifest"]["effective_market_policy_hash"],
                approved_package_hash=package.content_hash,
                target_market_profile_ref=target_market["ref"],
                target_market_profile_hash=target_market["content_hash"],
                market_alignment_dossier_ref=composite_alignment["ref"],
                market_alignment_dossier_hash=composite_alignment["content_hash"],
                approved_publish_window=evidence["approved_publish_window"],
            ),
            assigned_final_review_task_id=review.id,
            correlation_id="pkg1-sc04-revision-human-closeout-approval",
        )

        no_execution_after_approval = (
            self.revision_service.source_service._no_execution_counts()
        )
        if no_execution_after_approval != no_execution_before:
            raise ValidationFailureError("SC04_CLOSEOUT_EXECUTION_BOUNDARY_CHANGED")
        no_execution_deltas = {
            key: no_execution_after_approval[key] - value
            for key, value in no_execution_before.items()
        }
        no_execution_summary = {
            "provider_calls": sum(
                no_execution_deltas.get(key, 0)
                for key in (
                    "provider_attempts",
                    "provider_jobs",
                    "paid_provider_calls",
                )
            ),
            "render_calls": sum(
                no_execution_deltas.get(key, 0)
                for key in ("media_render_jobs", "final_media_refs")
            ),
            "drive_calls": sum(
                no_execution_deltas.get(key, 0)
                for key in ("media_offload_jobs", "cloud_media_refs")
            ),
            "youtube_calls": sum(
                no_execution_deltas.get(key, 0)
                for key in ("human_upload_tasks", "uploaded_videos")
            ),
        }
        receipt_content = {
            "schema_version": RECEIPT_SCHEMA_VERSION,
            "receipt_content_authority": "ARTIFACT_VERSION_CONTENT_HASH",
            "decided_by_user_id": str(command.decided_by_user_id),
            "decision": command.decision,
            "decision_source": command.decision_source,
            "review_authority": command.review_authority,
            "operator_decision_text": command.operator_decision_text,
            "review_notes": command.review_notes,
            "review_task_id": str(review.id),
            "approval_decision_id": str(approval.id),
            "approval_ref": command.approval_ref,
            "approval_scope": command.approval_scope,
            "reviewed_package": self.revision_service._version_ref(package),
            "revision": {
                "revision_id": str(command.reviewed_revision_id),
                "revision_version": command.reviewed_revision_version,
                "revision_hash": command.reviewed_revision_hash,
            },
            "effective_artifacts": deepcopy(
                evidence["manifest"]["effective_artifacts"]
            ),
            "effective_artifact_authority": deepcopy(
                evidence["manifest"]["effective_artifact_authority"]
            ),
            "composite_market_alignment_authority": deepcopy(composite_alignment),
            "exact_bindings": deepcopy(exact_bindings),
            "human_closeout": {
                "PKG1_SC04_REVISION_HUMAN_REVIEW": "PASS",
                "PKG1_SC04_REVISION_FINAL": "PASS",
                "PRODUCTION_PACKAGE_APPROVED": True,
                "MR1_EXECUTION": ("BLOCKED_REQUIRES_FRESH_SC04_PACKAGE_REAPPROVAL"),
                "PROCEED_TO_MR1_REAPPROVAL": True,
                "PROCEED_TO_MR1": False,
                "provider_execution_authorized": False,
                "render_execution_authorized": False,
                "publish_execution_authorized": False,
            },
            "superseded_mr1_approvals": superseded_mr1,
            "no_execution_proof": {
                "before_counts": no_execution_before,
                "after_counts": no_execution_after_approval,
                "deltas": no_execution_deltas,
                "all_deltas_zero": all(
                    value == 0 for value in no_execution_deltas.values()
                ),
                **no_execution_summary,
            },
        }
        receipt_artifact, receipt = self._create_receipt(
            project=project,
            actor_id=command.decided_by_user_id,
            content=receipt_content,
        )
        ReviewService(self.session).complete_review_task(
            review_task_id=review.id,
            actor_user_id=command.decided_by_user_id,
            resolution_ref=command.approval_ref,
            approval_decision_ids=[approval.id],
            correlation_id="pkg1-sc04-revision-human-closeout-review",
        )

        no_execution_after = self.revision_service.source_service._no_execution_counts()
        if no_execution_after != no_execution_before:
            raise ValidationFailureError("SC04_CLOSEOUT_EXECUTION_BOUNDARY_CHANGED")

        package_artifact.status = "approved"
        receipt_artifact.status = HUMAN_RECEIPT_ARTIFACT_STATUS
        project.status = "approved"
        project.audience_delivery_summary = {
            **deepcopy(project.audience_delivery_summary or {}),
            "production_package_status": "APPROVED",
            "sc04_revision_human_review": "PASS",
            "mr1_reapproval": "READY",
            "mr1_execution": "BLOCKED_REQUIRES_FRESH_SC04_PACKAGE_REAPPROVAL",
            "provider_execution": "NOT_AUTHORIZED",
            "upload_ready": False,
            "publish_execution_ready": False,
        }
        self.session.flush()
        result = self.read_closeout(project.id)
        result["no_execution_counts_before"] = no_execution_before
        result["no_execution_counts_after"] = no_execution_after
        return result

    def read_closeout(self, project_id: uuid.UUID) -> dict[str, Any]:
        project = self.session.get(VideoProject, project_id)
        if project is None or project.project_type != PROJECT_TYPE:
            raise NotFoundError(f"PKG1 SC-04 revision not found: {project_id}")
        validated_revision = self.revision_service._validated_current_revision_state(
            project
        )
        artifacts = validated_revision["artifacts"]
        package = artifacts.get("package_manifest")
        receipt = artifacts.get(RECEIPT_ARTIFACT_TYPE)
        if package is None or receipt is None:
            raise ValidationFailureError("SC04_CLOSEOUT_ARTIFACTS_MISSING")
        package_artifact = self.session.get(Artifact, package.artifact_id)
        receipt_artifact = self.session.get(Artifact, receipt.artifact_id)
        approvals = self._sc04_package_approvals(package.id)
        if len(approvals) != 1:
            raise ValidationFailureError("EXACT_SC04_PACKAGE_APPROVAL_REQUIRED")
        approval = approvals[0]
        try:
            review = self.session.get(
                ReviewTask, uuid.UUID(str(receipt.content["review_task_id"]))
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValidationFailureError("SC04_CLOSEOUT_REVIEW_REF_INVALID") from exc
        if (
            project.status != "approved"
            or package_artifact is None
            or package_artifact.status != "approved"
            or package_artifact.current_version_id != package.id
            or receipt_artifact is None
            or receipt_artifact.status != HUMAN_RECEIPT_ARTIFACT_STATUS
            or receipt_artifact.current_version_id != receipt.id
            or receipt.status != HUMAN_RECEIPT_VERSION_STATUS
            or content_hash(package.content) != package.content_hash
            or content_hash(receipt.content) != receipt.content_hash
            or review is None
            or review.status != "completed"
            or review.target_artifact_version_id != package.id
            or review.target_id != package.id
            or review.assigned_to_user_id != approval.decided_by_user_id
            or approval.target_type != "artifact_version"
            or approval.target_id != package.id
            or approval.target_artifact_version_id != package.id
            or approval.decision != "approved"
            or receipt.content.get("approval_decision_id") != str(approval.id)
            or receipt.content.get("approval_ref")
            != (approval.metadata_ or {}).get("approval_ref")
            or receipt.content.get("approval_scope") != APPROVAL_SCOPE
            or receipt.content.get("decision") != "PASS"
            or receipt.content.get("decision_source") != "OPERATOR"
            or receipt.content.get("review_authority") != "HUMAN"
            or receipt.content.get("operator_decision_text") != "PASS"
            or receipt.content.get("decided_by_user_id")
            != str(approval.decided_by_user_id)
            or (receipt.content.get("reviewed_package") or {}).get(
                "artifact_version_id"
            )
            != str(package.id)
            or (receipt.content.get("reviewed_package") or {}).get("content_hash")
            != package.content_hash
            or (approval.metadata_ or {}).get("package_artifact_version_id")
            != str(package.id)
            or (approval.metadata_ or {}).get("package_content_hash")
            != package.content_hash
            or approval.approved_package_hash != package.content_hash
            or (receipt.content.get("revision") or {}).get("revision_id")
            != package.content.get("revision_id")
            or (receipt.content.get("revision") or {}).get("revision_version")
            != package.content.get("revision_version")
            or (receipt.content.get("revision") or {}).get("revision_hash")
            != package.content.get("revision_hash")
            or (approval.metadata_ or {}).get("revision_id")
            != package.content.get("revision_id")
            or (approval.metadata_ or {}).get("revision_version")
            != package.content.get("revision_version")
            or (approval.metadata_ or {}).get("revision_hash")
            != package.content.get("revision_hash")
        ):
            raise ValidationFailureError("SC04_CLOSEOUT_AUTHORITY_INVALID")
        effective = receipt.content.get("human_closeout") or {}
        composite_alignment = (
            receipt.content.get("composite_market_alignment_authority") or {}
        )
        no_execution = receipt.content.get("no_execution_proof") or {}
        if (
            approval.policy_snapshot_id != project.policy_snapshot_id
            or approval.market_policy_hash
            != package.content.get("effective_market_policy_hash")
            or approval.market_alignment_dossier_ref != composite_alignment.get("ref")
            or approval.market_alignment_dossier_hash
            != composite_alignment.get("content_hash")
            or not approval.approved_publish_window
            or no_execution.get("all_deltas_zero") is not True
            or any((no_execution.get("deltas") or {}).values())
            or no_execution.get("before_counts") != no_execution.get("after_counts")
            or no_execution.get("provider_calls") != 0
            or no_execution.get("render_calls") != 0
            or no_execution.get("drive_calls") != 0
            or no_execution.get("youtube_calls") != 0
        ):
            raise ValidationFailureError("SC04_CLOSEOUT_STRICT_FIELDS_INVALID")
        bound_authority = self._revalidate_bound_source_and_geo_authority(
            project=project,
            manifest=package.content or {},
        )
        geo_closeout = bound_authority["geo_closeout"]
        target_market = (package.content.get("exact_bindings") or {}).get(
            "target_market_profile"
        ) or {}
        envelope = StrictMarketLineageEnvelope(
            policy_snapshot_id=project.policy_snapshot_id,
            approved_market_policy_hash=package.content["effective_market_policy_hash"],
            target_market_profile_ref=target_market["ref"],
            target_market_profile_hash=target_market["content_hash"],
            market_alignment_dossier_ref=composite_alignment["ref"],
            market_alignment_dossier_hash=composite_alignment["content_hash"],
            destination_binding_id=(
                geo_closeout.destination_runtime.destination_binding_id
            ),
            approved_destination_fingerprint=(
                geo_closeout.destination_runtime.binding_fingerprint
            ),
            approved_platform=geo_closeout.destination_runtime.platform,
            approved_platform_channel_id=(
                geo_closeout.destination_runtime.platform_channel_id
            ),
            approved_handle=geo_closeout.destination_runtime.handle,
            approved_package_hash=package.content_hash,
            approved_publish_timezone=(approval.metadata_ or {})[
                "approved_publish_timezone"
            ],
            approved_publish_window=approval.approved_publish_window or {},
            approval_decision_id=approval.id,
        )
        StrictMarketLineageService().validate_approval_record(
            envelope=envelope,
            approval=approval,
            approved_package_version=package,
            approved_package_artifact=package_artifact,
            expected_video_project_id=project.id,
        )
        if effective != {
            "PKG1_SC04_REVISION_HUMAN_REVIEW": "PASS",
            "PKG1_SC04_REVISION_FINAL": "PASS",
            "PRODUCTION_PACKAGE_APPROVED": True,
            "MR1_EXECUTION": "BLOCKED_REQUIRES_FRESH_SC04_PACKAGE_REAPPROVAL",
            "PROCEED_TO_MR1_REAPPROVAL": True,
            "PROCEED_TO_MR1": False,
            "provider_execution_authorized": False,
            "render_execution_authorized": False,
            "publish_execution_authorized": False,
        }:
            raise ValidationFailureError("SC04_CLOSEOUT_EFFECTIVE_STATE_INVALID")
        return {
            "video_project_id": str(project.id),
            "project_status": project.status,
            "package_artifact_version_id": str(package.id),
            "package_content_hash": package.content_hash,
            "package_artifact_status": package_artifact.status,
            "immutable_package_declared_status": package.content.get("package_status"),
            "review_task_id": str(review.id),
            "review_task_status": review.status,
            "approval_decision_id": str(approval.id),
            "approval_ref": (approval.metadata_ or {}).get("approval_ref"),
            "approval_scope": (approval.metadata_ or {}).get("approval_scope"),
            "decided_by_user_id": str(approval.decided_by_user_id),
            "decision": receipt.content["decision"],
            "decision_source": receipt.content["decision_source"],
            "review_authority": receipt.content["review_authority"],
            "operator_decision_text": receipt.content["operator_decision_text"],
            "review_notes": receipt.content.get("review_notes"),
            "human_review_receipt_artifact_id": str(receipt.artifact_id),
            "human_review_receipt_artifact_version_id": str(receipt.id),
            "human_review_receipt_content_hash": receipt.content_hash,
            "revision_id": receipt.content["revision"]["revision_id"],
            "revision_version": receipt.content["revision"]["revision_version"],
            "revision_hash": receipt.content["revision"]["revision_hash"],
            **deepcopy(effective),
            "provider_calls": no_execution["provider_calls"],
            "render_calls": no_execution["render_calls"],
            "drive_calls": no_execution["drive_calls"],
            "youtube_calls": no_execution["youtube_calls"],
        }

    def _revalidate_bound_source_and_geo_authority(
        self,
        *,
        project: VideoProject,
        manifest: dict[str, Any],
    ) -> dict[str, Any]:
        source_authority = manifest.get("source_human_authority") or {}
        approved_package = source_authority.get("approved_package") or {}
        approval = source_authority.get("approval") or {}
        human_receipt = source_authority.get("human_review_receipt") or {}
        overlay_ref = manifest.get("effective_monetization_policy") or {}
        geo_closeout_ref = manifest.get("geo_market_delivery_closeout_evidence") or {}
        try:
            source_project_id = uuid.UUID(str(source_authority["source_project_id"]))
            source_package_version_id = uuid.UUID(
                str(approved_package["artifact_version_id"])
            )
            source_package_hash = str(approved_package["content_hash"])
            source_approval_id = uuid.UUID(str(approval["approval_decision_id"]))
            source_receipt_version_id = uuid.UUID(
                str(human_receipt["artifact_version_id"])
            )
            source_receipt_hash = str(human_receipt["content_hash"])
            overlay_version_id = uuid.UUID(str(overlay_ref["artifact_version_id"]))
            overlay_hash = str(overlay_ref["content_hash"])
            geo_closeout_version_id = uuid.UUID(
                str(geo_closeout_ref["artifact_version_id"])
            )
            geo_closeout_hash = str(geo_closeout_ref["content_hash"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValidationFailureError("SC04_BOUND_AUTHORITY_REFS_INVALID") from exc

        (
            source_project,
            source_package,
            resolved_source_authority,
        ) = self.revision_service._approved_source_package(
            project.channel_workspace_id,
            requested_project_id=source_project_id,
            requested_package_version_id=source_package_version_id,
            requested_package_hash=source_package_hash,
            requested_approval_id=source_approval_id,
            requested_receipt_version_id=source_receipt_version_id,
            requested_receipt_hash=source_receipt_hash,
        )
        if (
            source_authority != resolved_source_authority
            or manifest.get("source_project_ref")
            != f"video-project://{source_project.id}"
            or manifest.get("supersedes")
            != self.revision_service._version_ref(source_package)
        ):
            raise ValidationFailureError("SC04_SOURCE_HUMAN_AUTHORITY_CHANGED")

        overlay = self.revision_service._resolve_ads_only_overlay(
            channel_id=project.channel_workspace_id,
            snapshot_id=project.policy_snapshot_id,
            requested_version_id=overlay_version_id,
            requested_hash=overlay_hash,
            requested_closeout_version_id=geo_closeout_version_id,
            requested_closeout_hash=geo_closeout_hash,
        )
        self.revision_service._validate_geo_source_authority(
            overlay=overlay,
            source_project=source_project,
            source_package=source_package,
            source_human_authority=resolved_source_authority,
        )
        if (
            overlay["ref"] != overlay_ref
            or overlay["closeout_ref"] != geo_closeout_ref
            or overlay["effective_market_policy_hash"]
            != manifest.get("effective_market_policy_hash")
        ):
            raise ValidationFailureError("SC04_GEO_AUTHORITY_CHANGED")
        closeout_version = self.session.get(ArtifactVersion, geo_closeout_version_id)
        try:
            geo_closeout = GeoMarketDeliveryCloseoutEvidence.model_validate(
                closeout_version.content if closeout_version is not None else {}
            )
        except Exception as exc:
            raise ValidationFailureError("SC04_CLOSEOUT_GEO_AUTHORITY_INVALID") from exc
        return {
            "source_project": source_project,
            "source_package": source_package,
            "source_human_authority": resolved_source_authority,
            "overlay": overlay,
            "geo_closeout": geo_closeout,
        }

    def _revalidate_exact_package(
        self,
        *,
        project: VideoProject,
        command: PKG1SC04RevisionApprovalCommand,
    ) -> dict[str, Any]:
        package = self.session.get(
            ArtifactVersion, command.reviewed_package_artifact_version_id
        )
        package_artifact = (
            self.session.get(Artifact, package.artifact_id)
            if package is not None
            else None
        )
        if (
            package is None
            or package_artifact is None
            or package_artifact.video_project_id != project.id
            or package_artifact.artifact_type != "package_manifest"
            or package_artifact.current_version_id != package.id
            or package.content_hash != command.reviewed_package_hash
            or content_hash(package.content) != package.content_hash
        ):
            raise ValidationFailureError("SC04_REVIEWED_PACKAGE_HASH_MISMATCH")
        manifest = package.content or {}
        if (
            manifest.get("revision_id") != str(command.reviewed_revision_id)
            or manifest.get("revision_version") != command.reviewed_revision_version
            or manifest.get("revision_hash") != command.reviewed_revision_hash
            or manifest.get("package_status") != "TECHNICAL_PASS_HUMAN_REVIEW_PENDING"
            or manifest.get("PKG1_SC04_REVISION_HUMAN_REVIEW") != "PENDING"
            or manifest.get("PRODUCTION_PACKAGE_APPROVED") is not False
            or (manifest.get("mr1_reapproval_manifest_compatibility_gate") or {}).get(
                "verdict"
            )
            != "PASS"
        ):
            raise ValidationFailureError("SC04_REVIEWED_REVISION_IDENTITY_MISMATCH")
        bound_authority = self._revalidate_bound_source_and_geo_authority(
            project=project,
            manifest=manifest,
        )
        effective = manifest.get("effective_artifacts") or {}
        authority = manifest.get("effective_artifact_authority") or {}
        composite_alignment = (
            authority.get("composite_market_alignment_authority") or {}
        )
        composite_core = {
            key: deepcopy(value)
            for key, value in composite_alignment.items()
            if key not in {"ref", "content_hash"}
        }
        if (
            composite_alignment.get("schema_version")
            != "pkg1.sc04-composite-alignment-authority.v1"
            or composite_alignment.get("revision_id") != manifest.get("revision_id")
            or composite_alignment.get("revision_hash") != manifest.get("revision_hash")
            or composite_alignment.get("subject") != effective.get("visual_plan")
            or composite_alignment.get("supplemental_visual_alignment")
            != effective.get("market_gate_results")
            or (composite_alignment.get("nonvisual_market_alignment") or {}).get(
                "authority_scope"
            )
            != "NONVISUAL_COMPONENTS_ONLY"
            or (composite_alignment.get("nonvisual_niche_alignment") or {}).get(
                "authority_scope"
            )
            != "NONVISUAL_COMPONENTS_ONLY"
            or composite_alignment.get("content_hash") != content_hash(composite_core)
            or composite_alignment.get("ref")
            != (
                "pkg1-sc04-composite-alignment://"
                f"{manifest.get('revision_id')}/"
                f"{composite_alignment.get('content_hash')}"
            )
        ):
            raise ValidationFailureError("SC04_COMPOSITE_ALIGNMENT_INVALID")
        project_ids = {
            uuid.UUID(str(value))
            for value in (authority.get("authority_project_ids") or {}).values()
            if value
        }
        if project.id not in project_ids or not effective:
            raise ValidationFailureError("SC04_EFFECTIVE_AUTHORITY_PROJECTS_INVALID")
        invalid: list[str] = []
        for key, ref in sorted(effective.items()):
            try:
                version = self.session.get(
                    ArtifactVersion, uuid.UUID(str(ref["artifact_version_id"]))
                )
            except (KeyError, TypeError, ValueError):
                version = None
            artifact = (
                self.session.get(Artifact, version.artifact_id)
                if version is not None
                else None
            )
            if (
                version is None
                or artifact is None
                or artifact.artifact_type != key
                or artifact.video_project_id not in project_ids
                or artifact.current_version_id != version.id
                or ref.get("artifact_id") != str(artifact.id)
                or ref.get("artifact_version_ref") != f"artifact-version://{version.id}"
                or ref.get("content_hash") != version.content_hash
                or content_hash(version.content) != version.content_hash
            ):
                invalid.append(key)
        if invalid:
            raise ValidationFailureError(
                "SC04_EFFECTIVE_ARTIFACT_HASH_MISMATCH:" + ",".join(invalid)
            )
        revised = manifest.get("revised_artifacts") or {}
        if content_hash(
            {key: ref["content_hash"] for key, ref in sorted(revised.items())}
        ) != manifest.get("planning_output_set_hash") or any(
            effective.get(key) != ref for key, ref in revised.items()
        ):
            raise ValidationFailureError("SC04_PLANNING_OUTPUT_SET_HASH_MISMATCH")
        geo_closeout = bound_authority["geo_closeout"]
        publish_ref = effective.get("publish_handoff_package") or {}
        try:
            publish_version = self.session.get(
                ArtifactVersion,
                uuid.UUID(str(publish_ref["artifact_version_id"])),
            )
        except (KeyError, TypeError, ValueError):
            publish_version = None
        publish_content = publish_version.content if publish_version else {}
        approved_publish_window = publish_content.get(
            "approved_publish_window"
        ) or publish_content.get("publish_window_hypothesis")
        approved_publish_timezone = publish_content.get("approved_publish_timezone")
        if (
            not isinstance(approved_publish_window, dict)
            or not approved_publish_window
            or not isinstance(approved_publish_timezone, str)
            or not approved_publish_timezone
        ):
            raise ValidationFailureError("SC04_APPROVED_PUBLISH_WINDOW_MISSING")
        source_package = bound_authority["source_package"]
        return {
            "package": package,
            "package_artifact": package_artifact,
            "manifest": manifest,
            "source_package": source_package,
            "geo_closeout": geo_closeout,
            "approved_publish_window": deepcopy(approved_publish_window),
            "approved_publish_timezone": approved_publish_timezone,
            "composite_alignment": deepcopy(composite_alignment),
        }

    def _superseded_mr1_approvals(
        self,
        *,
        source_package: ArtifactVersion,
        manifest: dict[str, Any],
    ) -> list[dict[str, Any]]:
        rows = list(
            self.session.scalars(
                select(ApprovalDecision).where(
                    ApprovalDecision.target_artifact_version_id == source_package.id,
                    ApprovalDecision.decision == "approved",
                )
            ).all()
        )
        by_id = {str(item.id): item for item in rows}
        attempts = (manifest.get("attempt_evidence") or {}).get("attempts") or []
        if len(attempts) != 2:
            raise ValidationFailureError("SC04_SUPERSEDED_MR1_ATTEMPT_EVIDENCE_INVALID")
        expected = {
            str(attempts[0].get("approval_id")): ("MR1_REAL_PRODUCTION_EXECUTION"),
            str(attempts[1].get("continuation_approval_id")): (
                "MR1_EXACT_PROVIDER_ATTEMPT_CONTINUATION"
            ),
        }
        if "None" in expected or len(expected) != 2:
            raise ValidationFailureError("SC04_SUPERSEDED_MR1_AUTHORITY_IDS_MISSING")
        for approval_id, scope in expected.items():
            approval = by_id.get(approval_id)
            if (
                approval is None
                or approval.decision != "approved"
                or approval.target_artifact_version_id != source_package.id
                or (approval.metadata_ or {}).get("approval_scope") != scope
            ):
                raise ValidationFailureError("SC04_SUPERSEDED_MR1_AUTHORITY_MISMATCH")
        historical_receipts = manifest.get("superseded_approvals") or []
        historical_by_id = {
            str(item.get("approval_decision_id")): item for item in historical_receipts
        }
        if not historical_receipts or any(
            item.get("reuse_allowed") is not False
            or item.get("historical_receipt_mutated") is not False
            for item in historical_receipts
        ):
            raise ValidationFailureError(
                "SC04_SOURCE_SUPERSEDED_APPROVAL_RECEIPTS_INVALID"
            )
        if any(
            historical_by_id.get(approval_id, {}).get("approval_scope") != scope
            for approval_id, scope in expected.items()
        ):
            raise ValidationFailureError(
                "SC04_SOURCE_SUPERSEDED_MR1_RECEIPT_SET_INCOMPLETE"
            )
        result = [
            {
                "approval_decision_id": str(item.id),
                "approval_scope": (item.metadata_ or {}).get("approval_scope"),
                "decision": item.decision,
                "historical_receipt_mutated": False,
                "reuse_allowed": False,
                "superseded_by": "PKG1_SC04_REVISION_HUMAN_PASS",
            }
            for item in sorted(rows, key=lambda value: str(value.id))
            if (item.metadata_ or {}).get("approval_scope") in _SUPERSEDED_MR1_SCOPES
        ]
        result_ids = {item["approval_decision_id"] for item in result}
        if not set(expected).issubset(result_ids):
            raise ValidationFailureError("SC04_SUPERSEDED_MR1_AUTHORITY_SET_INCOMPLETE")
        return result

    def _sc04_package_approvals(self, package_id: uuid.UUID) -> list[ApprovalDecision]:
        return [
            item
            for item in self.session.scalars(
                select(ApprovalDecision).where(
                    ApprovalDecision.target_artifact_version_id == package_id,
                    ApprovalDecision.decision == "approved",
                )
            ).all()
            if (item.metadata_ or {}).get("approval_scope") == APPROVAL_SCOPE
        ]

    def _create_receipt(
        self,
        *,
        project: VideoProject,
        actor_id: uuid.UUID,
        content: dict[str, Any],
    ) -> tuple[Artifact, ArtifactVersion]:
        service = ArtifactService(self.session)
        artifact = service.create_artifact(
            data=ArtifactCreate(
                video_project_id=project.id,
                artifact_type=RECEIPT_ARTIFACT_TYPE,
                status="in_review",
                created_by_user_id=actor_id,
            ),
            correlation_id="pkg1-sc04-revision-human-closeout-receipt",
        )
        version = service.create_artifact_version(
            data=ArtifactVersionCreate(
                artifact_id=artifact.id,
                content=deepcopy(content),
                status="submitted",
                created_by_user_id=actor_id,
                evidence_refs=[
                    {
                        "type": "reviewed_package",
                        "artifact_version_id": content["reviewed_package"][
                            "artifact_version_id"
                        ],
                        "content_hash": content["reviewed_package"]["content_hash"],
                    },
                    {
                        "type": "approval_decision",
                        "id": content["approval_decision_id"],
                    },
                ],
                context_refs=[
                    {
                        "type": "pkg1_sc04_human_closeout",
                        "revision_hash": content["revision"]["revision_hash"],
                    }
                ],
            ),
            correlation_id="pkg1-sc04-revision-human-closeout-receipt-version",
        )
        return artifact, version

    @staticmethod
    def _validate_existing_command(
        *,
        command: PKG1SC04RevisionApprovalCommand,
        result: dict[str, Any],
    ) -> None:
        if (
            result["package_artifact_version_id"]
            != str(command.reviewed_package_artifact_version_id)
            or result["package_content_hash"] != command.reviewed_package_hash
            or result["review_task_id"] != str(command.review_task_id)
            or result["revision_id"] != str(command.reviewed_revision_id)
            or result["revision_version"] != command.reviewed_revision_version
            or result["revision_hash"] != command.reviewed_revision_hash
            or result["approval_ref"] != command.approval_ref
            or result["decided_by_user_id"] != str(command.decided_by_user_id)
            or result["decision"] != command.decision
            or result["decision_source"] != command.decision_source
            or result["review_authority"] != command.review_authority
            or result["operator_decision_text"] != command.operator_decision_text
            or result["approval_scope"] != command.approval_scope
            or result["review_notes"] != command.review_notes
        ):
            raise ValidationFailureError("SC04_EXISTING_CLOSEOUT_COMMAND_MISMATCH")
