from __future__ import annotations

import hashlib
import json
import shutil
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contracts.channel_policy import ChannelScopedPolicy
from app.contracts.mr1 import MR1ReapprovalCommand
from app.contracts.pkg1_market_revision_closeout import (
    PKG1MarketRevisionApprovalCommand,
)
from app.contracts.workflow import (
    ApprovalDecisionCreate,
    ArtifactCreate,
    ArtifactVersionCreate,
)
from app.core.config import Settings
from app.core.errors import NotFoundError, ValidationFailureError
from app.db.models import (
    ApprovalDecision,
    Artifact,
    ArtifactVersion,
    ChannelProfileVersion,
    ChannelWorkspace,
    CompiledChannelPolicySnapshot,
    ReviewTask,
    VideoProject,
)
from app.services.config_registry import ConfigRegistryService, content_hash
from app.services.pkg1_market_revision import (
    DRIVE_IDEMPOTENCY_PHASES,
    LPRO1_ORCHESTRATOR_VERSION,
    LPRO1_RENDER_CONTRACT_VERSION,
    PROJECT_TYPE,
    PKG1MarketRevisionService,
)
from app.services.pkg1_market_revision_closeout import (
    APPROVAL_SCOPE as PKG1_APPROVAL_SCOPE,
    PKG1MarketRevisionCloseoutService,
)
from app.services.pkg1_sc04_revision_closeout import (
    HUMAN_RECEIPT_ARTIFACT_STATUS as SC04_HUMAN_RECEIPT_ARTIFACT_STATUS,
    HUMAN_RECEIPT_VERSION_STATUS as SC04_HUMAN_RECEIPT_VERSION_STATUS,
    PKG1SC04RevisionCloseoutService,
)
from app.services.workflow import ApprovalService, ArtifactService


APPROVAL_SCOPE = "MR1_REAL_PRODUCTION_EXECUTION"
RECEIPT_ARTIFACT_TYPE = "mr1_execution_approval_receipt"
READINESS_ARTIFACT_TYPE = "mr1_execution_readiness_preflight"
SUPERSESSION_ARTIFACT_TYPE = "mr1_approval_supersession_ledger"
REUSE_DECISION_ARTIFACT_TYPE = "mr1_reuse_decision_manifest"

SC04_PROJECT_TYPE = "PKG1_SC04_REVISION"
SC04_EFFECTIVE_AUTHORITY_SCHEMA = "pkg1.sc04-effective-authority.v1"
SC04_EFFECTIVE_RESOLVER_CONTRACT = "EFFECTIVE_ARTIFACTS_V1"
SC04_SUPPLEMENTAL_VISUAL_ARTIFACT_TYPE = "market_gate_results"
SC04_PKG1_APPROVAL_SCOPE = "PKG1_SC04_REVISION_PACKAGE_PLANNING"
SC04_HUMAN_RECEIPT_ARTIFACT_TYPE = "pkg1_sc04_revision_human_review_receipt"

# These are the concrete planning authorities reopened from the database before
# either an MR1 approval or an MR1 run can be created.  PKG1_SC04_REVISION must
# expose them through its explicit composite authority; silently merging package
# sections would make a superseded visual dossier look current again.
MR1_REQUIRED_EFFECTIVE_ARTIFACT_TYPES = {
    "script",
    "spoken_text_normalized",
    "narration_pacing_preflight_estimate",
    "voice_policy",
    "visual_direction_contract",
    "visual_plan",
    "visual_source_decision_set",
    "compiled_asset_request_plan",
    "niche_alignment_dossier",
    "market_alignment_dossier",
    "provider_execution_plan",
    "cost_estimate_snapshot",
    "rights_disclosure_completeness_report",
    "synthetic_media_disclosure_receipt_draft",
    "asset_provenance_plan",
    "publish_risk_dossier",
    "target_market_profile",
    "target_market_digest",
    "destination_binding",
}

APPROVED_OPERATIONS = [
    "ELEVENLABS_NARRATION_UNDER_APPROVED_VOICE_POLICY",
    "ELEVENLABS_FORCED_ALIGNMENT_UNDER_APPROVED_ALIGNMENT_CONTRACT",
    "PEXELS_APPROVED_ELIGIBLE_SOURCE_DECISIONS_ONLY",
    "GOOGLE_GEMINI_IMAGE_APPROVED_IMAGE_DECISIONS_ONLY",
    "GOOGLE_VEO_APPROVED_MOTION_VALUE_DECISIONS_ONLY",
    "NATIVE_DIAGRAM_MOTION_EDITORIAL_ASSETS",
    "MEDIA_NORMALIZATION",
    "NATIVE_MOTION_COMPILER",
    "NATIVE_FFMPEG_RENDERER",
    "TECHNICAL_MEDIA_QC",
    "CREATIVE_PERCEPTUAL_MEDIA_QC",
    "GOOGLE_DRIVE_CANONICAL_REVIEW_ARCHIVE_EXPORT_AND_VERIFICATION",
    "GOOGLE_DRIVE_FINALIZATION_SUPPLEMENT_EXPORT_AND_VERIFICATION_AFTER_HUMAN_PASS",
    "HUMAN_FULL_WATCH_CLOSEOUT",
    "FINAL_MEDIA_REF_AFTER_ALL_REQUIRED_GATES",
]

PROHIBITED_OPERATIONS = [
    "SCRIPT_REWRITE",
    "TOPIC_CHANGE",
    "UNAPPROVED_VISUAL_SCENE_OR_ROUTE",
    "METADATA_REWRITE",
    "THUMBNAIL_REWRITE",
    "DESTINATION_CHANGE",
    "NEW_PROVIDER",
    "PROVIDER_SUBSTITUTION",
    "AUTOMATIC_PEXELS_TO_AI_FAILOVER",
    "EXTERNAL_AI_VIDEO_FALLBACK",
    "YOUTUBE_UPLOAD",
    "AUTO_PUBLISH",
]

PASS_VERDICTS = (
    "MR1_REAPPROVAL_ENTRY",
    "MR1_REAPPROVAL_EXACT_TARGET",
    "MR1_REAPPROVAL_HASH_REVALIDATION",
    "MR1_REAPPROVAL_PROFILE_V3_BINDING",
    "MR1_REAPPROVAL_TARGET_MARKET_BINDING",
    "MR1_REAPPROVAL_MARKET_ALIGNMENT",
    "MR1_REAPPROVAL_DESTINATION_BINDING",
    "MR1_REAPPROVAL_PROVIDER_PLAN",
    "MR1_REAPPROVAL_COST_SCOPE",
    "MR1_REAPPROVAL_RIGHTS_DISCLOSURE",
    "MR1_REAPPROVAL_LPRO1_CONTRACT",
    "MR1_REAPPROVAL_APPROVAL_RECEIPT",
    "MR1_REAPPROVAL_READINESS",
    "MR1_REAPPROVAL_PUBLISH_BOUNDARY",
)
SC04_REUSE_PASS_VERDICT = "MR1_REUSE_DECISIONS"

REVISED_BINDING_TYPES = (
    "market_alignment_dossier",
    "niche_alignment_dossier",
    "provider_execution_plan",
    "cost_estimate_snapshot",
    "rights_disclosure_completeness_report",
    "synthetic_media_disclosure_receipt_draft",
    "asset_provenance_plan",
    "publish_risk_dossier",
)


class MR1ReapprovalService:
    """Create one immutable, exact-target authority without starting MR1."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.revision_service = PKG1MarketRevisionService(session)
        self.closeout_service = PKG1MarketRevisionCloseoutService(session)
        self.sc04_closeout_service = PKG1SC04RevisionCloseoutService(session)

    def approve(self, command: MR1ReapprovalCommand) -> dict[str, Any]:
        project = self.session.scalar(
            select(VideoProject)
            .where(VideoProject.id == command.project_id)
            .with_for_update()
        )
        if project is None:
            raise NotFoundError(
                f"approved PKG1 revision not found: {command.project_id}"
            )
        if project.project_type not in {PROJECT_TYPE, SC04_PROJECT_TYPE}:
            raise ValidationFailureError("MR1_TARGET_IS_NOT_SUPPORTED_PKG1_REVISION")
        if project.status != "approved":
            raise ValidationFailureError("MR1_TARGET_PACKAGE_IS_NOT_APPROVED")

        evidence = self._revalidate(command=command, project=project)
        existing = self._scope_approvals(evidence["package"].id)
        if existing:
            if len(existing) != 1:
                raise ValidationFailureError(
                    "MULTIPLE_MR1_REAPPROVALS_FOR_EXACT_PACKAGE"
                )
            result = self.read_approval(project.id)
            self._validate_existing_command(command=command, result=result)
            counts = self.revision_service._no_execution_counts()
            result["no_execution_counts_before"] = counts
            result["no_execution_counts_after"] = counts
            return result

        no_execution_before = self.revision_service._no_execution_counts()
        derived = self._derive_approval_scope(command=command, evidence=evidence)
        operator_id: uuid.UUID = evidence["pkg1_approval"].decided_by_user_id
        reuse_decision: ArtifactVersion | None = None
        if project.project_type == SC04_PROJECT_TYPE:
            _, reuse_decision = self._create_artifact(
                project=project,
                artifact_type=REUSE_DECISION_ARTIFACT_TYPE,
                content=self._reuse_decision_content(
                    evidence=evidence,
                    derived=derived,
                ),
                actor_id=operator_id,
                evidence_refs=[
                    {
                        "type": "approved_sc04_package",
                        "artifact_version_id": str(evidence["package"].id),
                        "content_hash": evidence["package"].content_hash,
                    }
                ],
            )
        approval_ref = self._approval_ref(command=command, evidence=evidence)
        reuse_authorized_keys = (
            list((reuse_decision.content or {}).get("reuse_allowed_output_keys"))
            if reuse_decision is not None
            else []
        )
        approval_decision_authority = self._approval_decision_authority_payload(
            project_type=project.project_type,
            project_id=project.id,
            approval_version=command.approval_version,
            mr1_approval_ref=approval_ref,
            decision=command.decision,
            decision_source=command.decision_source,
            approval_purpose=command.approval_purpose,
            execution_mode=command.execution_mode,
            run_type=command.run_type,
            channel_key=command.channel_key,
            operator_decision_text=command.operator_decision_text,
            exact_target=derived["exact_target"],
            exact_bindings=derived["exact_bindings"],
            provider_attempt_scope=derived["provider_attempt_scope"],
            cost_scope=derived["cost_scope"],
            destination=derived["destination"],
            human_and_final_media_policy=derived["human_and_final_media_policy"],
            reuse_allowed_output_keys=reuse_authorized_keys,
            reuse_decision_ref=(
                PKG1MarketRevisionService._version_ref(reuse_decision)
                if reuse_decision is not None
                else None
            ),
        )

        approval = ApprovalService(self.session).create_approval_decision(
            data=ApprovalDecisionCreate(
                target_type="artifact_version",
                target_id=evidence["package"].id,
                target_artifact_version_id=evidence["package"].id,
                decision="approved",
                decided_by_user_id=operator_id,
                rationale=(
                    "Explicit operator authority for one exact MR1 real-production "
                    "execution; publishing remains prohibited."
                ),
                metadata=deepcopy(approval_decision_authority["metadata"]),
                decision_basis=deepcopy(approval_decision_authority["decision_basis"]),
                evidence_basis=deepcopy(approval_decision_authority["evidence_basis"]),
                policy_basis=deepcopy(approval_decision_authority["policy_basis"]),
                context_pack_ref=(f"artifact-version://{evidence['package'].id}"),
                human_decision_note=(
                    "The operator supplied the explicit approval in the MR1 "
                    "re-approval prompt; Codex persisted but did not originate it."
                ),
                policy_snapshot_id=(
                    evidence["pkg1_approval"].policy_snapshot_id
                    if project.project_type == SC04_PROJECT_TYPE
                    else None
                ),
                destination_binding_id=(
                    evidence["pkg1_approval"].destination_binding_id
                    if project.project_type == SC04_PROJECT_TYPE
                    else None
                ),
                destination_binding_fingerprint=(
                    evidence["pkg1_approval"].destination_binding_fingerprint
                    if project.project_type == SC04_PROJECT_TYPE
                    else None
                ),
                market_policy_hash=(
                    evidence["pkg1_approval"].market_policy_hash
                    if project.project_type == SC04_PROJECT_TYPE
                    else None
                ),
                approved_package_hash=(
                    evidence["package"].content_hash
                    if project.project_type == SC04_PROJECT_TYPE
                    else None
                ),
                target_market_profile_ref=(
                    evidence["pkg1_approval"].target_market_profile_ref
                    if project.project_type == SC04_PROJECT_TYPE
                    else None
                ),
                target_market_profile_hash=(
                    evidence["pkg1_approval"].target_market_profile_hash
                    if project.project_type == SC04_PROJECT_TYPE
                    else None
                ),
                market_alignment_dossier_ref=(
                    evidence["pkg1_approval"].market_alignment_dossier_ref
                    if project.project_type == SC04_PROJECT_TYPE
                    else None
                ),
                market_alignment_dossier_hash=(
                    evidence["pkg1_approval"].market_alignment_dossier_hash
                    if project.project_type == SC04_PROJECT_TYPE
                    else None
                ),
                approved_publish_window=(
                    evidence["pkg1_approval"].approved_publish_window
                    if project.project_type == SC04_PROJECT_TYPE
                    else None
                ),
            ),
            assigned_final_review_task_id=evidence["review_task"].id,
            correlation_id="mr1-real-production-reapproval",
        )

        supersession_content = self._supersession_content(
            command=command,
            approval=approval,
            approval_ref=approval_ref,
            evidence=evidence,
        )
        _, supersession = self._create_artifact(
            project=project,
            artifact_type=SUPERSESSION_ARTIFACT_TYPE,
            content=supersession_content,
            actor_id=operator_id,
            evidence_refs=[
                {
                    "type": "historical_mr1_approval",
                    "approval_decision_id": supersession_content[
                        "superseded_approvals"
                    ][0]["approval_decision_id"],
                    "preservation_state": "PRESERVED",
                }
            ],
        )

        readiness_content = self._readiness_content(
            command=command,
            approval=approval,
            approval_ref=approval_ref,
            derived=derived,
            supersession=supersession,
            reuse_decision=reuse_decision,
        )
        _, readiness = self._create_artifact(
            project=project,
            artifact_type=READINESS_ARTIFACT_TYPE,
            content=readiness_content,
            actor_id=operator_id,
            evidence_refs=[
                {
                    "type": "approved_package",
                    "artifact_version_id": str(evidence["package"].id),
                    "content_hash": evidence["package"].content_hash,
                },
                {
                    "type": "approval_decision",
                    "approval_decision_id": str(approval.id),
                    "approval_ref": approval_ref,
                },
            ],
        )

        receipt_content = self._receipt_content(
            command=command,
            approval=approval,
            approval_ref=approval_ref,
            derived=derived,
            supersession=supersession,
            readiness=readiness,
            reuse_decision=reuse_decision,
            approval_decision_authority=approval_decision_authority,
        )
        _, receipt = self._create_artifact(
            project=project,
            artifact_type=RECEIPT_ARTIFACT_TYPE,
            content=receipt_content,
            actor_id=operator_id,
            evidence_refs=[
                {
                    "type": "pkg1_human_review_receipt",
                    "artifact_version_id": str(evidence["pkg1_receipt"].id),
                    "content_hash": evidence["pkg1_receipt"].content_hash,
                },
                {
                    "type": "mr1_readiness",
                    "artifact_version_id": str(readiness.id),
                    "content_hash": readiness.content_hash,
                },
            ],
        )
        self.session.flush()

        no_execution_after = self.revision_service._no_execution_counts()
        if no_execution_after != no_execution_before:
            raise ValidationFailureError("MR1_REAPPROVAL_EXECUTION_BOUNDARY_CHANGED")

        result = self.read_approval(project.id)
        result["no_execution_counts_before"] = no_execution_before
        result["no_execution_counts_after"] = no_execution_after
        if result["approval_receipt_artifact_version_id"] != str(receipt.id):
            raise ValidationFailureError("MR1_APPROVAL_RECEIPT_READBACK_MISMATCH")
        return result

    def read_approval(self, project_id: uuid.UUID) -> dict[str, Any]:
        project = self.session.get(VideoProject, project_id)
        if project is None or project.project_type not in {
            PROJECT_TYPE,
            SC04_PROJECT_TYPE,
        }:
            raise NotFoundError(f"MR1 re-approval not found: {project_id}")
        if project.status != "approved":
            raise ValidationFailureError("MR1_APPROVED_PROJECT_REQUIRED")
        artifacts = self._exact_mr1_artifacts(project_id)
        package = self.revision_service._current_artifacts(project_id).get(
            "package_manifest"
        )
        if package is None:
            raise ValidationFailureError("MR1_APPROVED_PACKAGE_MISSING")
        self.resolve_package_artifact_authority(
            project=project,
            package=package,
        )
        approvals = self._scope_approvals(package.id)
        if len(approvals) != 1:
            raise ValidationFailureError("EXACT_MR1_REAPPROVAL_REQUIRED")
        approval = approvals[0]
        receipt = artifacts[RECEIPT_ARTIFACT_TYPE]
        readiness = artifacts[READINESS_ARTIFACT_TYPE]
        supersession = artifacts[SUPERSESSION_ARTIFACT_TYPE]
        reuse_decision = artifacts.get(REUSE_DECISION_ARTIFACT_TYPE)
        hash_checks = [
            (receipt, "MR1_APPROVAL_RECEIPT_HASH_MISMATCH"),
            (readiness, "MR1_READINESS_HASH_MISMATCH"),
            (supersession, "MR1_SUPERSESSION_LEDGER_HASH_MISMATCH"),
        ]
        if reuse_decision is not None:
            hash_checks.append(
                (reuse_decision, "MR1_REUSE_DECISION_MANIFEST_HASH_MISMATCH")
            )
        for version, reason in hash_checks:
            if content_hash(version.content or {}) != version.content_hash:
                raise ValidationFailureError(reason)

        receipt_content = receipt.content or {}
        readiness_content = readiness.content or {}
        supersession_content = supersession.content or {}
        if (
            receipt_content.get("approval_decision_id") != str(approval.id)
            or receipt_content.get("decision") != "APPROVED"
            or receipt_content.get("decision_source") != "OPERATOR"
            or receipt_content.get("approval_purpose") != APPROVAL_SCOPE
            or receipt_content.get("readiness", {}).get("artifact_version_id")
            != str(readiness.id)
            or receipt_content.get("readiness", {}).get("content_hash")
            != readiness.content_hash
            or receipt_content.get("supersession_ledger", {}).get("artifact_version_id")
            != str(supersession.id)
            or receipt_content.get("supersession_ledger", {}).get("content_hash")
            != supersession.content_hash
            or readiness_content.get("approval_decision_id") != str(approval.id)
            or supersession_content.get("replacement_approval_decision_id")
            != str(approval.id)
        ):
            raise ValidationFailureError("MR1_APPROVAL_ARTIFACT_LINEAGE_MISMATCH")
        if project.project_type == SC04_PROJECT_TYPE:
            self._revalidate_sc04_read_authority(
                project=project,
                receipt_content=receipt_content,
                package=package,
                approval=approval,
            )
        if project.project_type == SC04_PROJECT_TYPE:
            if reuse_decision is None:
                raise ValidationFailureError("MR1_REUSE_DECISION_MANIFEST_REQUIRED")
            expected_reuse_ref = PKG1MarketRevisionService._version_ref(reuse_decision)
            reuse_content = reuse_decision.content or {}
            reuse_entries = {
                item.get("output_key"): item
                for item in reuse_content.get("entries") or []
                if isinstance(item, dict)
            }
            reuse_allowed = reuse_content.get("reuse_allowed_output_keys")
            reuse_classifications = {
                str(item.get("output_key")): str(item.get("classification"))
                for item in reuse_content.get("entries") or []
                if isinstance(item, dict) and item.get("output_key")
            }
            allowed_scope = {"narration_audio", "forced_alignment"}
            valid_reuse_lineage = bool(
                isinstance(reuse_allowed, list)
                and set(reuse_allowed).issubset(allowed_scope)
                and reuse_allowed
                in (
                    [],
                    ["narration_audio"],
                    ["narration_audio", "forced_alignment"],
                )
                and reuse_content.get("prior_output_reuse_count") == len(reuse_allowed)
                and all(
                    (reuse_entries.get(key) or {}).get("classification")
                    == "REUSE_VALID"
                    and (reuse_entries.get(key) or {}).get("reuse_authorized") is True
                    for key in reuse_allowed
                )
                and reuse_content.get("canonical_timeline_reuse_authorized") is False
                and reuse_content.get("supporting_visual_subwindows_reuse_authorized")
                is False
                and reuse_content.get("fresh_temporal_compilation_required") is True
                and reuse_content.get("fresh_caption_compilation_required") is True
                and readiness_content.get("prior_output_reuse_count")
                == len(reuse_allowed)
                and readiness_content.get("reuse_allowed_output_keys") == reuse_allowed
                and readiness_content.get("reuse_classifications_hash")
                == content_hash(reuse_classifications)
                and readiness_content.get("reuse_manifest_content_hash")
                == reuse_decision.content_hash
                and readiness_content.get("fresh_provider_call_plan")
                == reuse_content.get("fresh_provider_call_plan")
                and readiness_content.get("fresh_temporal_compilation_required") is True
                and (approval.metadata_ or {}).get("reuse_allowed_output_keys")
                == reuse_allowed
                and (approval.metadata_ or {}).get("immutable_output_reuse_authorized")
                == bool(reuse_allowed)
                and (approval.metadata_ or {}).get(
                    "old_approval_run_attempt_authority_reused"
                )
                is False
                and (approval.metadata_ or {}).get(
                    "canonical_timeline_reuse_authorized"
                )
                is False
            )
            if (
                receipt_content.get("reuse_decision_manifest") != expected_reuse_ref
                or readiness_content.get("reuse_decision_manifest")
                != expected_reuse_ref
                or reuse_content.get("target_package", {}).get("artifact_version_id")
                != str(package.id)
                or reuse_content.get("target_package", {}).get("content_hash")
                != package.content_hash
                or reuse_content.get("fail_closed") is not True
                or not valid_reuse_lineage
            ):
                raise ValidationFailureError(
                    "MR1_REUSE_DECISION_MANIFEST_LINEAGE_INVALID"
                )

        hash_payload = deepcopy(receipt_content)
        approval_content_hash = hash_payload.pop("approval_content_hash", None)
        if not isinstance(
            approval_content_hash, str
        ) or approval_content_hash != content_hash(hash_payload):
            raise ValidationFailureError("MR1_APPROVAL_CONTENT_HASH_MISMATCH")
        self._revalidate_approval_decision_authority(
            project=project,
            approval=approval,
            receipt_content=receipt_content,
            reuse_decision=reuse_decision,
        )

        exact_target = deepcopy(receipt_content["exact_target"])
        if (
            exact_target.get("project_id") != str(project.id)
            or exact_target.get("package_artifact_version_id") != str(package.id)
            or exact_target.get("package_content_hash") != package.content_hash
        ):
            raise ValidationFailureError("MR1_APPROVAL_EXACT_TARGET_STALE")
        if project.project_type == SC04_PROJECT_TYPE:
            try:
                pkg1_approval = self.session.get(
                    ApprovalDecision,
                    uuid.UUID(exact_target["pkg1_approval_decision_id"]),
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise ValidationFailureError(
                    "MR1_SC04_STRICT_LINEAGE_REF_INVALID"
                ) from exc
            strict_fields = (
                "policy_snapshot_id",
                "destination_binding_id",
                "destination_binding_fingerprint",
                "market_policy_hash",
                "target_market_profile_ref",
                "target_market_profile_hash",
                "market_alignment_dossier_ref",
                "market_alignment_dossier_hash",
                "approved_publish_window",
            )
            if (
                pkg1_approval is None
                or approval.approved_package_hash != package.content_hash
                or any(
                    getattr(approval, key) != getattr(pkg1_approval, key)
                    for key in strict_fields
                )
            ):
                raise ValidationFailureError("MR1_SC04_STRICT_LINEAGE_BINDING_MISMATCH")

        counts = self.revision_service._no_execution_counts()
        result: dict[str, Any] = {
            "approval_decision_id": str(approval.id),
            "approval_id": str(approval.id),
            "approval_version": receipt_content["approval_version"],
            "approval_ref": receipt_content["approval_ref"],
            "approval_content_hash": approval_content_hash,
            "approval_receipt_artifact_version_id": str(receipt.id),
            "approval_receipt_content_hash": receipt.content_hash,
            "readiness_artifact_version_id": str(readiness.id),
            "readiness_content_hash": readiness.content_hash,
            "supersession_artifact_version_id": str(supersession.id),
            "supersession_content_hash": supersession.content_hash,
            **(
                {
                    "reuse_decision_artifact_version_id": str(reuse_decision.id),
                    "reuse_decision_content_hash": reuse_decision.content_hash,
                    "reuse_decision": deepcopy(reuse_decision.content or {}),
                }
                if reuse_decision is not None
                else {}
            ),
            "exact_target": exact_target,
            "exact_bindings": deepcopy(receipt_content["exact_bindings"]),
            "provider_attempt_scope": deepcopy(
                receipt_content["provider_attempt_scope"]
            ),
            "cost_scope": deepcopy(receipt_content["cost_scope"]),
            "destination": deepcopy(receipt_content["destination"]),
            "human_and_final_media_policy": deepcopy(
                receipt_content["human_and_final_media_policy"]
            ),
            "lpro1_execution_contract": deepcopy(
                receipt_content["lpro1_execution_contract"]
            ),
            "readiness": deepcopy(readiness_content),
            "no_execution_counts_before": counts,
            "no_execution_counts_after": counts,
            "MR1_REAPPROVAL_FINAL": "PASS",
            "MR1_EXECUTION": "NOT_STARTED",
            "MR1_PROVIDER_CALL_COUNT": 0,
            "MR1_RENDER_STATUS": "NOT_STARTED",
            "MR1_HUMAN_REVIEW": "PENDING",
            "PUBLISH_DESTINATION_STATUS": "PENDING_PLATFORM_ID",
            "PUBLISH_EXECUTION_READY": False,
            "PROCEED_TO_MR1": True,
            "provider_calls": 0,
            "render_calls": 0,
            "drive_calls": 0,
            "youtube_calls": 0,
        }
        result.update({key: "PASS" for key in PASS_VERDICTS})
        if project.project_type == SC04_PROJECT_TYPE:
            result[SC04_REUSE_PASS_VERDICT] = "PASS"
        return result

    def _revalidate_sc04_read_authority(
        self,
        *,
        project: VideoProject,
        receipt_content: dict[str, Any],
        package: ArtifactVersion,
        approval: ApprovalDecision,
    ) -> None:
        """Reopen the exact SC-04 closeout authority on every approval read."""

        exact_target = receipt_content.get("exact_target") or {}
        exact_bindings = receipt_content.get("exact_bindings") or {}
        profile_binding = exact_bindings.get("channel_profile_version") or {}
        snapshot_binding = exact_bindings.get("compiled_channel_policy_snapshot") or {}
        try:
            command = MR1ReapprovalCommand(
                project_id=project.id,
                pkg1_approval_decision_id=uuid.UUID(
                    str(exact_target["pkg1_approval_decision_id"])
                ),
                pkg1_human_review_receipt_version_id=uuid.UUID(
                    str(exact_target["pkg1_human_review_receipt_version_id"])
                ),
                channel_profile_version_id=uuid.UUID(str(profile_binding["id"])),
                compiled_policy_snapshot_id=uuid.UUID(str(snapshot_binding["id"])),
                approval_version=int(receipt_content["approval_version"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValidationFailureError("MR1_SC04_READ_AUTHORITY_REF_INVALID") from exc
        evidence = self._revalidate_sc04(command=command, project=project)
        if (
            evidence["package"].id != package.id
            or evidence["package"].content_hash != package.content_hash
            or evidence["pkg1_approval"].id != command.pkg1_approval_decision_id
            or evidence["pkg1_receipt"].id
            != command.pkg1_human_review_receipt_version_id
            or approval.target_artifact_version_id != package.id
        ):
            raise ValidationFailureError("MR1_SC04_READ_AUTHORITY_LINEAGE_MISMATCH")

    def _revalidate(
        self,
        *,
        command: MR1ReapprovalCommand,
        project: VideoProject,
    ) -> dict[str, Any]:
        if project.project_type == SC04_PROJECT_TYPE:
            return self._revalidate_sc04(command=command, project=project)

        closeout = self.closeout_service.read_closeout(project.id)
        if (
            closeout.get("PKG1_MARKET_REVISION_FINAL") != "PASS"
            or closeout.get("PRODUCTION_PACKAGE_APPROVED") is not True
            or closeout.get("MR1_REAPPROVAL_ENTRY") != "READY"
            or closeout.get("MR1_EXECUTION") != "NOT_STARTED"
            or closeout.get("MR1_PROVIDER_CALL_COUNT") != 0
            or closeout.get("MR1_RENDER_STATUS") != "NOT_STARTED"
            or closeout.get("MR1_HUMAN_REVIEW") != "PENDING"
            or closeout.get("PROCEED_TO_MR1_REAPPROVAL") is not True
            or closeout.get("PROCEED_TO_MR1") is not False
        ):
            raise ValidationFailureError("MR1_REAPPROVAL_ENTRY_NOT_READY")
        if closeout["approval_decision_id"] != str(command.pkg1_approval_decision_id):
            raise ValidationFailureError("PKG1_APPROVAL_DECISION_MISMATCH")
        if closeout["human_review_receipt_artifact_version_id"] != str(
            command.pkg1_human_review_receipt_version_id
        ):
            raise ValidationFailureError("PKG1_HUMAN_RECEIPT_MISMATCH")

        pkg1_approval = self.session.get(
            ApprovalDecision, command.pkg1_approval_decision_id
        )
        pkg1_receipt = self.session.get(
            ArtifactVersion, command.pkg1_human_review_receipt_version_id
        )
        if pkg1_approval is None or pkg1_receipt is None:
            raise ValidationFailureError("PKG1_APPROVAL_OR_RECEIPT_MISSING")
        if (
            pkg1_approval.decision != "approved"
            or (pkg1_approval.metadata_ or {}).get("approval_scope")
            != PKG1_APPROVAL_SCOPE
            or (pkg1_approval.metadata_ or {}).get("decision_source") != "OPERATOR"
            or content_hash(pkg1_receipt.content or {}) != pkg1_receipt.content_hash
            or pkg1_receipt.content.get("decision") != "PASS"
            or pkg1_receipt.content.get("decision_source") != "OPERATOR"
            or pkg1_receipt.content.get("review_authority") != "HUMAN"
        ):
            raise ValidationFailureError("PKG1_APPROVAL_AUTHORITY_INVALID")

        review_task = self.session.get(
            ReviewTask, uuid.UUID(pkg1_receipt.content["review_task_id"])
        )
        if (
            review_task is None
            or review_task.status != "completed"
            or review_task.assigned_to_user_id != pkg1_approval.decided_by_user_id
        ):
            raise ValidationFailureError("PKG1_HUMAN_REVIEW_AUTHORITY_INVALID")

        package_id = uuid.UUID(closeout["package_artifact_version_id"])
        package = self.session.get(ArtifactVersion, package_id)
        if (
            package is None
            or pkg1_approval.target_artifact_version_id != package.id
            or review_task.target_artifact_version_id != package.id
        ):
            raise ValidationFailureError("PKG1_EXACT_PACKAGE_AUTHORITY_MISMATCH")

        closeout_command = PKG1MarketRevisionApprovalCommand(
            project_id=project.id,
            review_task_id=review_task.id,
            reviewed_package_artifact_version_id=package.id,
            reviewed_package_hash=closeout["package_content_hash"],
            reviewed_revision_id=uuid.UUID(closeout["revision_id"]),
            reviewed_revision_version=closeout["revision_version"],
            reviewed_revision_hash=closeout["revision_hash"],
            decided_by_user_id=pkg1_approval.decided_by_user_id,
            decision="PASS",
            decision_source="OPERATOR",
            review_authority="HUMAN",
            operator_decision_text="PASS",
            approval_ref=(pkg1_approval.metadata_ or {})["approval_ref"],
            review_notes=pkg1_receipt.content.get("review_notes"),
        )
        evidence = self.closeout_service._revalidate_exact_hashes(
            project=project,
            command=closeout_command,
        )
        bindings = evidence["exact_bindings"]
        if (
            (bindings.get("channel_profile_version") or {}).get("id")
            != str(command.channel_profile_version_id)
            or (bindings.get("channel_profile_version") or {}).get("version") != 3
            or (bindings.get("compiled_channel_policy_snapshot") or {}).get("id")
            != str(command.compiled_policy_snapshot_id)
        ):
            raise ValidationFailureError("MR1_PROFILE_V3_SNAPSHOT_BINDING_MISMATCH")

        channel = self.session.get(ChannelWorkspace, project.channel_workspace_id)
        if channel is None or channel.key != command.channel_key:
            raise ValidationFailureError("MR1_CHANNEL_KEY_MISMATCH")
        active_revision_ids = set(
            self.session.scalars(
                select(VideoProject.id).where(
                    VideoProject.channel_workspace_id == channel.id,
                    VideoProject.project_type == PROJECT_TYPE,
                    VideoProject.status != "archived",
                )
            ).all()
        )
        if active_revision_ids != {project.id}:
            raise ValidationFailureError(
                "EXACTLY_ONE_CANONICAL_MARKET_REVISION_REQUIRED"
            )

        # The immutable revision/package lineage above revalidates the active
        # profile and snapshot against this DB.  Upstream closeout reports are
        # additionally checked for their verdicts only: fixture databases have
        # deliberately different UUIDs from the production report activation.
        reports = self.revision_service._load_entry_reports()
        if (
            reports["lpro1"].get("result") != "PASS"
            or (reports["geo1"].get("verdicts") or {}).get("GEO1_FINAL") != "PASS"
            or (reports["geo2"].get("verdicts") or {}).get("GEO2_FINAL") != "PASS"
            or (reports["ch1"].get("verdicts") or {}).get("CH1_MARKET_V3_FINAL")
            != "PASS"
        ):
            raise ValidationFailureError("MR1_REQUIRED_UPSTREAM_GATE_NOT_PASS")

        evidence.update(
            {
                "project": project,
                "package": package,
                "pkg1_approval": pkg1_approval,
                "pkg1_receipt": pkg1_receipt,
                "review_task": review_task,
                "closeout": closeout,
                "entry_reports": reports,
                "reviewed_snapshot_hash": pkg1_receipt.content[
                    "reviewed_snapshot_hash"
                ],
            }
        )
        return evidence

    def _revalidate_sc04(
        self,
        *,
        command: MR1ReapprovalCommand,
        project: VideoProject,
    ) -> dict[str, Any]:
        """Reopen the explicit SC-04 human PASS authority, never infer it."""

        closeout_read_model = self.sc04_closeout_service.read_closeout(project.id)
        if (
            closeout_read_model.get("PKG1_SC04_REVISION_FINAL") != "PASS"
            or closeout_read_model.get("PRODUCTION_PACKAGE_APPROVED") is not True
            or closeout_read_model.get("PROCEED_TO_MR1_REAPPROVAL") is not True
            or closeout_read_model.get("PROCEED_TO_MR1") is not False
            or closeout_read_model.get("approval_decision_id")
            != str(command.pkg1_approval_decision_id)
            or closeout_read_model.get("human_review_receipt_artifact_version_id")
            != str(command.pkg1_human_review_receipt_version_id)
        ):
            raise ValidationFailureError("MR1_SC04_REAPPROVAL_ENTRY_NOT_READY")

        artifacts = self.revision_service._current_artifacts(project.id)
        package = artifacts.get("package_manifest")
        receipt = artifacts.get(SC04_HUMAN_RECEIPT_ARTIFACT_TYPE)
        if package is None or receipt is None:
            raise ValidationFailureError("MR1_SC04_HUMAN_CLOSEOUT_ARTIFACTS_MISSING")
        package_artifact = self.session.get(Artifact, package.artifact_id)
        receipt_artifact = self.session.get(Artifact, receipt.artifact_id)
        if (
            project.status != "approved"
            or package_artifact is None
            or package_artifact.video_project_id != project.id
            or package_artifact.artifact_type != "package_manifest"
            or package_artifact.current_version_id != package.id
            or package_artifact.status != "approved"
            or package.status not in {"submitted", "approved"}
            or receipt_artifact is None
            or receipt_artifact.video_project_id != project.id
            or receipt_artifact.artifact_type != SC04_HUMAN_RECEIPT_ARTIFACT_TYPE
            or receipt_artifact.current_version_id != receipt.id
            or receipt_artifact.status != SC04_HUMAN_RECEIPT_ARTIFACT_STATUS
            or receipt.status != SC04_HUMAN_RECEIPT_VERSION_STATUS
            or content_hash(package.content or {}) != package.content_hash
            or content_hash(receipt.content or {}) != receipt.content_hash
        ):
            raise ValidationFailureError("MR1_SC04_CLOSEOUT_STORAGE_AUTHORITY_INVALID")

        pkg1_approval = self.session.get(
            ApprovalDecision, command.pkg1_approval_decision_id
        )
        if receipt.id != command.pkg1_human_review_receipt_version_id:
            raise ValidationFailureError("PKG1_SC04_HUMAN_RECEIPT_MISMATCH")
        receipt_content = receipt.content or {}
        manifest = package.content or {}
        reviewed_package = receipt_content.get("reviewed_package") or {}
        revision = receipt_content.get("revision") or {}
        human_closeout = receipt_content.get("human_closeout") or {}
        if (
            pkg1_approval is None
            or pkg1_approval.decision != "approved"
            or pkg1_approval.target_artifact_version_id != package.id
            or (pkg1_approval.metadata_ or {}).get("approval_scope")
            != SC04_PKG1_APPROVAL_SCOPE
            or (pkg1_approval.metadata_ or {}).get("decision_source") != "OPERATOR"
            or (pkg1_approval.metadata_ or {}).get("review_authority") != "HUMAN"
            or (pkg1_approval.metadata_ or {}).get("package_artifact_version_id")
            != str(package.id)
            or (pkg1_approval.metadata_ or {}).get("package_content_hash")
            != package.content_hash
            or (pkg1_approval.metadata_ or {}).get("revision_id")
            != manifest.get("revision_id")
            or (pkg1_approval.metadata_ or {}).get("revision_version")
            != manifest.get("revision_version")
            or (pkg1_approval.metadata_ or {}).get("revision_hash")
            != manifest.get("revision_hash")
            or pkg1_approval.market_alignment_dossier_ref
            != (
                (manifest.get("effective_artifact_authority") or {})
                .get("composite_market_alignment_authority", {})
                .get("ref")
            )
            or pkg1_approval.market_alignment_dossier_hash
            != (
                (manifest.get("effective_artifact_authority") or {})
                .get("composite_market_alignment_authority", {})
                .get("content_hash")
            )
            or (pkg1_approval.metadata_ or {}).get("mr1_execution_authorized")
            is not False
            or (pkg1_approval.metadata_ or {}).get("provider_execution_authorized")
            is not False
            or (pkg1_approval.metadata_ or {}).get("publish_execution_authorized")
            is not False
        ):
            raise ValidationFailureError("PKG1_SC04_APPROVAL_AUTHORITY_INVALID")
        if (
            receipt_content.get("schema_version") != "pkg1.sc04-human-review-receipt.v1"
            or receipt_content.get("receipt_content_authority")
            != "ARTIFACT_VERSION_CONTENT_HASH"
            or receipt_content.get("decision") != "PASS"
            or receipt_content.get("decision_source") != "OPERATOR"
            or receipt_content.get("review_authority") != "HUMAN"
            or receipt_content.get("operator_decision_text") != "PASS"
            or receipt_content.get("approval_decision_id") != str(pkg1_approval.id)
            or receipt_content.get("approval_scope") != SC04_PKG1_APPROVAL_SCOPE
            or reviewed_package.get("artifact_version_id") != str(package.id)
            or reviewed_package.get("content_hash") != package.content_hash
            or revision.get("revision_id") != manifest.get("revision_id")
            or revision.get("revision_version") != manifest.get("revision_version")
            or revision.get("revision_hash") != manifest.get("revision_hash")
            or receipt_content.get("effective_artifacts")
            != manifest.get("effective_artifacts")
            or receipt_content.get("effective_artifact_authority")
            != manifest.get("effective_artifact_authority")
            or receipt_content.get("composite_market_alignment_authority")
            != (
                (manifest.get("effective_artifact_authority") or {}).get(
                    "composite_market_alignment_authority"
                )
            )
            or receipt_content.get("exact_bindings") != manifest.get("exact_bindings")
        ):
            raise ValidationFailureError("PKG1_SC04_HUMAN_RECEIPT_INVALID")
        if human_closeout != {
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
            raise ValidationFailureError("PKG1_SC04_HUMAN_CLOSEOUT_BOUNDARY_INVALID")

        try:
            review_id = uuid.UUID(str(receipt_content["review_task_id"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValidationFailureError("PKG1_SC04_REVIEW_TASK_REF_INVALID") from exc
        review_task = self.session.get(ReviewTask, review_id)
        if (
            review_task is None
            or review_task.status != "completed"
            or review_task.video_project_id != project.id
            or review_task.review_type != "final_human"
            or review_task.target_artifact_version_id != package.id
            or review_task.target_id != package.id
            or review_task.assigned_to_user_id != pkg1_approval.decided_by_user_id
        ):
            raise ValidationFailureError("PKG1_SC04_HUMAN_REVIEW_AUTHORITY_INVALID")

        superseded = receipt_content.get("superseded_mr1_approvals") or []
        if not superseded:
            raise ValidationFailureError("PKG1_SC04_SUPERSEDED_MR1_AUTHORITY_MISSING")
        for item in superseded:
            try:
                old_approval = self.session.get(
                    ApprovalDecision, uuid.UUID(str(item["approval_decision_id"]))
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise ValidationFailureError(
                    "PKG1_SC04_SUPERSEDED_MR1_REF_INVALID"
                ) from exc
            if (
                old_approval is None
                or old_approval.decision != item.get("decision")
                or (old_approval.metadata_ or {}).get("approval_scope")
                != item.get("approval_scope")
                or item.get("historical_receipt_mutated") is not False
                or item.get("reuse_allowed") is not False
                or item.get("superseded_by") != "PKG1_SC04_REVISION_HUMAN_PASS"
            ):
                raise ValidationFailureError(
                    "PKG1_SC04_SUPERSEDED_MR1_AUTHORITY_INVALID"
                )

        no_execution = receipt_content.get("no_execution_proof") or {}
        if (
            no_execution.get("all_deltas_zero") is not True
            or any(value != 0 for value in (no_execution.get("deltas") or {}).values())
            or any(
                no_execution.get(key, 0) != 0
                for key in (
                    "provider_calls",
                    "render_calls",
                    "drive_calls",
                    "youtube_calls",
                )
            )
        ):
            raise ValidationFailureError("PKG1_SC04_CLOSEOUT_EXECUTION_DELTA_INVALID")

        exact_bindings = deepcopy(manifest.get("exact_bindings") or {})
        frozen = self._frozen_market_evidence(
            project=project,
            exact_bindings=exact_bindings,
            command=command,
        )
        channel = self.session.get(ChannelWorkspace, project.channel_workspace_id)
        if channel is None or channel.key != command.channel_key:
            raise ValidationFailureError("MR1_CHANNEL_KEY_MISMATCH")
        active_sc04_ids = set(
            self.session.scalars(
                select(VideoProject.id).where(
                    VideoProject.channel_workspace_id == channel.id,
                    VideoProject.project_type == SC04_PROJECT_TYPE,
                    VideoProject.status != "archived",
                )
            ).all()
        )
        if active_sc04_ids != {project.id}:
            raise ValidationFailureError("EXACTLY_ONE_CANONICAL_SC04_REVISION_REQUIRED")

        reports = self.revision_service._load_entry_reports()
        if (
            reports["lpro1"].get("result") != "PASS"
            or (reports["geo1"].get("verdicts") or {}).get("GEO1_FINAL") != "PASS"
            or (reports["geo2"].get("verdicts") or {}).get("GEO2_FINAL") != "PASS"
            or (reports["ch1"].get("verdicts") or {}).get("CH1_MARKET_V3_FINAL")
            != "PASS"
        ):
            raise ValidationFailureError("MR1_REQUIRED_UPSTREAM_GATE_NOT_PASS")

        # Reopen every effective ref and the supplemental visual authority now,
        # before an MR1 approval record can be appended.
        self.resolve_package_artifact_authority(project=project, package=package)
        return {
            "project": project,
            "package": package,
            "pkg1_approval": pkg1_approval,
            "pkg1_receipt": receipt,
            "review_task": review_task,
            "closeout": human_closeout,
            "entry_reports": reports,
            "exact_bindings": exact_bindings,
            "reviewed_snapshot_hash": content_hash(
                {
                    "effective_artifacts": manifest["effective_artifacts"],
                    "exact_bindings": exact_bindings,
                }
            ),
            **frozen,
        }

    def _frozen_market_evidence(
        self,
        *,
        project: VideoProject,
        exact_bindings: dict[str, Any],
        command: MR1ReapprovalCommand,
    ) -> dict[str, Any]:
        profile_binding = exact_bindings.get("channel_profile_version") or {}
        snapshot_binding = exact_bindings.get("compiled_channel_policy_snapshot") or {}
        try:
            profile_id = uuid.UUID(str(profile_binding["id"]))
            snapshot_id = uuid.UUID(str(snapshot_binding["id"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValidationFailureError(
                "MR1_PROFILE_SNAPSHOT_BINDING_INVALID"
            ) from exc
        profile = self.session.get(ChannelProfileVersion, profile_id)
        snapshot = self.session.get(CompiledChannelPolicySnapshot, snapshot_id)
        channel = self.session.get(ChannelWorkspace, project.channel_workspace_id)
        if (
            profile is None
            or snapshot is None
            or channel is None
            or channel.status != "active"
            or channel.active_policy_snapshot_id != snapshot.id
            or profile.status not in {"active", "approved"}
            or snapshot.status != "active"
            or profile.channel_workspace_id != channel.id
            or snapshot.channel_workspace_id != channel.id
            or profile.id != command.channel_profile_version_id
            or snapshot.id != command.compiled_policy_snapshot_id
            or profile.version != 3
            or profile_binding.get("version") != 3
            or profile_binding.get("content_hash") != profile.profile_input_hash
            or content_hash(profile.profile_input) != profile.profile_input_hash
            or snapshot_binding.get("version") != snapshot.snapshot_version
            or snapshot_binding.get("content_hash") != snapshot.content_hash
            or content_hash(snapshot.compiled_payload) != snapshot.content_hash
            or snapshot.channel_profile_version_id != profile.id
            or snapshot.profile_input_hash != profile.profile_input_hash
            or project.channel_profile_version_id != profile.id
            or project.policy_snapshot_id != snapshot.id
        ):
            raise ValidationFailureError("MR1_PROFILE_V3_SNAPSHOT_BINDING_MISMATCH")
        try:
            policy = ChannelScopedPolicy.model_validate(
                (snapshot.compiled_payload or {}).get("channel_scoped_policy")
            )
        except Exception as exc:
            raise ValidationFailureError("MR1_FROZEN_CHANNEL_POLICY_INVALID") from exc
        market = policy.target_market_profile
        digest = policy.target_market_digest
        destination_policy = policy.destination_binding_policy
        if market is None or digest is None or destination_policy is None:
            raise ValidationFailureError("MR1_FROZEN_MARKET_AUTHORITY_MISSING")
        destination = destination_policy.destination
        if (
            (exact_bindings.get("target_market_profile") or {}).get("content_hash")
            != market.content_hash
            or (exact_bindings.get("target_market_digest") or {}).get("content_hash")
            != digest.content_hash
            or (exact_bindings.get("destination_binding") or {}).get("content_hash")
            != destination.content_hash
        ):
            raise ValidationFailureError("MR1_FROZEN_MARKET_BINDING_MISMATCH")
        return {
            "profile": profile,
            "snapshot": snapshot,
            "market_profile": market.model_dump(mode="json"),
            "market_digest": digest.model_dump(mode="json"),
            "destination": destination.model_dump(mode="json"),
        }

    def resolve_package_artifact_authority(
        self,
        *,
        project: VideoProject,
        package: ArtifactVersion,
    ) -> dict[str, Any]:
        """Resolve the immutable artifact set consumed by a future MR1 run.

        Legacy market revisions keep their original merged resolver.  SC-04
        revisions must provide an explicit composite authority so an old market
        or niche dossier can only contribute its nonvisual components.
        """

        manifest = package.content or {}
        if content_hash(manifest) != package.content_hash:
            raise ValidationFailureError("MR1_PACKAGE_CONTENT_HASH_MISMATCH")
        manifest_project_type = manifest.get("project_type")
        if project.project_type == SC04_PROJECT_TYPE:
            if manifest_project_type != SC04_PROJECT_TYPE:
                raise ValidationFailureError("MR1_PACKAGE_PROJECT_TYPE_MISMATCH")
        elif project.project_type != PROJECT_TYPE or manifest_project_type not in {
            None,
            PROJECT_TYPE,
        }:
            raise ValidationFailureError("MR1_PACKAGE_PROJECT_TYPE_INVALID")

        if project.project_type == PROJECT_TYPE:
            refs = {
                **deepcopy(manifest.get("reused_artifacts") or {}),
                **deepcopy(manifest.get("revised_artifacts") or {}),
            }
            missing = sorted(MR1_REQUIRED_EFFECTIVE_ARTIFACT_TYPES - set(refs))
            if missing:
                raise ValidationFailureError(
                    "MR1_AUTHORITY_REFS_MISSING:" + ",".join(missing)
                )
            versions = {
                key: self._version_from_ref(refs[key], key)
                for key in sorted(MR1_REQUIRED_EFFECTIVE_ARTIFACT_TYPES)
            }
            return {
                "variant": PROJECT_TYPE,
                "refs": refs,
                "versions": versions,
                "supplemental_visual_alignment": None,
                "authority_project_ids": None,
            }

        authority = manifest.get("effective_artifact_authority") or {}
        refs = deepcopy(manifest.get("effective_artifacts") or {})
        compatibility = manifest.get("mr1_reapproval_manifest_compatibility_gate") or {}
        if (
            authority.get("schema_version") != SC04_EFFECTIVE_AUTHORITY_SCHEMA
            or authority.get("resolver_contract") != SC04_EFFECTIVE_RESOLVER_CONTRACT
            or compatibility.get("schema_version")
            != "pkg1.sc04-mr1-manifest-compatibility.v1"
            or compatibility.get("verdict") != "PASS"
            or compatibility.get("resolver_contract")
            != SC04_EFFECTIVE_RESOLVER_CONTRACT
            or compatibility.get("provider_execution_authorized") is not False
        ):
            raise ValidationFailureError("MR1_SC04_EFFECTIVE_AUTHORITY_NOT_PASS")
        required_declared = set(
            compatibility.get("required_effective_artifact_types") or []
        )
        if not MR1_REQUIRED_EFFECTIVE_ARTIFACT_TYPES.issubset(required_declared):
            raise ValidationFailureError(
                "MR1_SC04_COMPATIBILITY_REQUIRED_SET_INCOMPLETE"
            )
        compatibility_checks = compatibility.get("checks") or {}
        if not compatibility_checks or any(
            value is not True for value in compatibility_checks.values()
        ):
            raise ValidationFailureError("MR1_SC04_COMPATIBILITY_CHECK_NOT_PASS")

        raw_project_ids = authority.get("authority_project_ids") or {}
        if set(raw_project_ids) != {
            "current_revision",
            "source_revision",
            "historical_source",
        }:
            raise ValidationFailureError("MR1_SC04_AUTHORITY_PROJECT_SET_INVALID")
        try:
            authority_project_ids = {
                key: uuid.UUID(str(value)) for key, value in raw_project_ids.items()
            }
        except (TypeError, ValueError) as exc:
            raise ValidationFailureError(
                "MR1_SC04_AUTHORITY_PROJECT_ID_INVALID"
            ) from exc
        if authority_project_ids["current_revision"] != project.id:
            raise ValidationFailureError("MR1_SC04_CURRENT_PROJECT_BINDING_MISMATCH")
        source_ref = manifest.get("source_project_ref")
        if source_ref != (
            f"video-project://{authority_project_ids['source_revision']}"
        ):
            raise ValidationFailureError("MR1_SC04_SOURCE_PROJECT_REF_MISMATCH")
        source_project = self.session.get(
            VideoProject, authority_project_ids["source_revision"]
        )
        historical_project = self.session.get(
            VideoProject, authority_project_ids["historical_source"]
        )
        if (
            source_project is None
            or source_project.project_type != PROJECT_TYPE
            or source_project.channel_workspace_id != project.channel_workspace_id
            or historical_project is None
            or historical_project.channel_workspace_id != project.channel_workspace_id
        ):
            raise ValidationFailureError("MR1_SC04_CROSS_PROJECT_LINEAGE_INVALID")

        source_package_ref = manifest.get("supersedes") or {}
        source_package = self._version_from_ref(
            source_package_ref,
            "package_manifest",
            allowed_project_ids={source_project.id},
        )
        historical_ref = (
            (source_package.content or {})
            .get("exact_bindings", {})
            .get("historical_video_project", {})
            .get("ref")
        )
        if historical_ref != f"video-project://{historical_project.id}":
            raise ValidationFailureError("MR1_SC04_HISTORICAL_PROJECT_REF_MISMATCH")

        missing = sorted(MR1_REQUIRED_EFFECTIVE_ARTIFACT_TYPES - set(refs))
        if missing:
            raise ValidationFailureError(
                "MR1_SC04_EFFECTIVE_ARTIFACTS_MISSING:" + ",".join(missing)
            )
        allowed_project_ids = set(authority_project_ids.values())
        versions = {
            key: self._version_from_ref(
                refs[key], key, allowed_project_ids=allowed_project_ids
            )
            for key in sorted(MR1_REQUIRED_EFFECTIVE_ARTIFACT_TYPES)
        }

        nonvisual = authority.get("nonvisual_reuse_authorities") or {}
        superseded = authority.get("superseded_visual_authorities") or []
        dossier_keys = {"market_alignment_dossier", "niche_alignment_dossier"}
        if (
            set(nonvisual) != dossier_keys
            or any(nonvisual[key] != refs[key] for key in dossier_keys)
            or {item.get("artifact_type") for item in superseded} != dossier_keys
        ):
            raise ValidationFailureError("MR1_SC04_NONVISUAL_DOSSIER_AUTHORITY_INVALID")
        for item in superseded:
            artifact_type = item.get("artifact_type")
            ref = {
                key: value
                for key, value in item.items()
                if key not in {"artifact_type", "authority_scope"}
            }
            if (
                item.get("authority_scope") != "NONVISUAL_COMPONENTS_ONLY"
                or artifact_type not in dossier_keys
                or ref != refs[artifact_type]
            ):
                raise ValidationFailureError(
                    "MR1_SC04_SUPERSEDED_VISUAL_AUTHORITY_INVALID"
                )

        visual_authority = authority.get("current_visual_authority") or {}
        if visual_authority.get("binding_key") != "supplemental_visual_alignment":
            raise ValidationFailureError("MR1_SC04_VISUAL_BINDING_KEY_INVALID")
        visual_ref = {
            key: value
            for key, value in visual_authority.items()
            if key != "binding_key"
        }
        exact_bindings = manifest.get("exact_bindings") or {}
        if exact_bindings.get("supplemental_visual_alignment") != visual_ref:
            raise ValidationFailureError("MR1_SC04_VISUAL_EXACT_BINDING_MISMATCH")
        visual_version = self._version_from_ref(
            visual_ref,
            SC04_SUPPLEMENTAL_VISUAL_ARTIFACT_TYPE,
            allowed_project_ids={project.id},
        )
        visual_content = visual_version.content or {}
        if (
            visual_content.get("revision_id") != manifest.get("revision_id")
            or visual_content.get("revision_hash") != manifest.get("revision_hash")
            or visual_content.get("subject") != refs["visual_plan"]
            or (visual_content.get("market_alignment") or {}).get("verdict") != "PASS"
            or (visual_content.get("niche_alignment") or {}).get("verdict") != "PASS"
            or visual_content.get("all_required_checks_pass") is not True
        ):
            raise ValidationFailureError(
                "MR1_SC04_SUPPLEMENTAL_VISUAL_ALIGNMENT_NOT_EXACT_PASS"
            )
        composite = authority.get("composite_market_alignment_authority") or {}
        if composite != exact_bindings.get("composite_market_alignment_authority"):
            raise ValidationFailureError("MR1_SC04_COMPOSITE_AUTHORITY_MISMATCH")
        composite_hash_payload = {
            key: deepcopy(value)
            for key, value in composite.items()
            if key not in {"ref", "content_hash"}
        }
        market_nonvisual = deepcopy(composite.get("nonvisual_market_alignment") or {})
        niche_nonvisual = deepcopy(composite.get("nonvisual_niche_alignment") or {})
        market_scope = market_nonvisual.pop("authority_scope", None)
        niche_scope = niche_nonvisual.pop("authority_scope", None)
        expected_composite_ref = (
            f"pkg1-sc04-composite-alignment://{manifest.get('revision_id')}/"
            f"{content_hash(composite_hash_payload)}"
        )
        if (
            composite.get("schema_version")
            != "pkg1.sc04-composite-alignment-authority.v1"
            or composite.get("revision_id") != manifest.get("revision_id")
            or composite.get("revision_hash") != manifest.get("revision_hash")
            or composite.get("subject") != refs["visual_plan"]
            or market_scope != "NONVISUAL_COMPONENTS_ONLY"
            or niche_scope != "NONVISUAL_COMPONENTS_ONLY"
            or market_nonvisual != refs["market_alignment_dossier"]
            or niche_nonvisual != refs["niche_alignment_dossier"]
            or composite.get("supplemental_visual_alignment") != visual_ref
            or composite.get("ref") != expected_composite_ref
            or composite.get("content_hash") != content_hash(composite_hash_payload)
        ):
            raise ValidationFailureError(
                "MR1_SC04_COMPOSITE_ALIGNMENT_AUTHORITY_INVALID"
            )
        return {
            "variant": SC04_PROJECT_TYPE,
            "refs": refs,
            "versions": versions,
            "supplemental_visual_alignment": visual_version,
            "authority_project_ids": {
                key: str(value) for key, value in authority_project_ids.items()
            },
        }

    def _derive_approval_scope(
        self,
        *,
        command: MR1ReapprovalCommand,
        evidence: dict[str, Any],
    ) -> dict[str, Any]:
        package: ArtifactVersion = evidence["package"]
        manifest = package.content or {}
        project: VideoProject = evidence["project"]
        artifact_authority = self.resolve_package_artifact_authority(
            project=project,
            package=package,
        )
        effective = artifact_authority["refs"]
        exact_bindings = deepcopy(evidence["exact_bindings"])
        for key in REVISED_BINDING_TYPES:
            if key not in effective:
                raise ValidationFailureError(f"MR1_REQUIRED_BINDING_MISSING:{key}")
            exact_bindings[key] = deepcopy(effective[key])
        for key in (
            "target_market_profile",
            "target_market_digest",
            "destination_binding",
        ):
            exact_bindings[f"{key}_artifact"] = deepcopy(effective[key])
        if artifact_authority["variant"] == SC04_PROJECT_TYPE:
            visual_version: ArtifactVersion = artifact_authority[
                "supplemental_visual_alignment"
            ]
            exact_bindings["supplemental_visual_alignment"] = {
                "artifact_id": str(visual_version.artifact_id),
                "artifact_version_id": str(visual_version.id),
                "artifact_version_ref": f"artifact-version://{visual_version.id}",
                "version_number": visual_version.version_number,
                "content_hash": visual_version.content_hash,
            }
            exact_bindings["market_alignment_dossier_visual_scope"] = (
                "HISTORICAL_NONVISUAL_COMPONENTS_ONLY"
            )
            exact_bindings["niche_alignment_dossier_visual_scope"] = (
                "HISTORICAL_NONVISUAL_COMPONENTS_ONLY"
            )

        provider_version = artifact_authority["versions"]["provider_execution_plan"]
        cost_version = artifact_authority["versions"]["cost_estimate_snapshot"]
        provider_plan = provider_version.content or {}
        cost = cost_version.content or {}
        source_provider_plan: dict[str, Any] | None = None
        if artifact_authority["variant"] == SC04_PROJECT_TYPE:
            source_revision_id = uuid.UUID(
                artifact_authority["authority_project_ids"]["source_revision"]
            )
            source_provider_version = self._version_from_ref(
                provider_plan.get("supersedes") or {},
                "provider_execution_plan",
                allowed_project_ids={source_revision_id},
            )
            source_provider_plan = source_provider_version.content or {}
        self._validate_provider_plan(
            plan=provider_plan,
            revised=effective,
            revision_id=manifest["revision_id"],
            revision_hash=manifest["revision_hash"],
            source_plan=source_provider_plan,
        )
        self._validate_cost_scope(
            cost=cost,
            revised=effective,
            reused=effective,
            provider_plan=provider_plan,
        )
        self._validate_market_rights(
            evidence=evidence,
            revised=effective,
            artifact_authority=artifact_authority,
        )

        lpro_contract_payload = {
            "schema_version": "mr1.lpro1-execution-contract.v1",
            "production_render_envelope_version": (
                "lpro1.production-render-envelope.v1"
            ),
            "orchestrator_version": exact_bindings[
                "lpro1_production_orchestrator_version"
            ],
            "render_contract_version": exact_bindings[
                "lpro1_production_contract_version"
            ],
            "execution_mode": command.execution_mode,
            "production_eligible": True,
            "single_run": True,
            "terminal_after_execution_begins": True,
            "package_ref": f"artifact-version://{package.id}",
            "package_hash": package.content_hash,
            "provider_execution_plan": deepcopy(effective["provider_execution_plan"]),
            "cost_estimate_snapshot": deepcopy(effective["cost_estimate_snapshot"]),
            "approved_operations": APPROVED_OPERATIONS,
            "prohibited_operations": PROHIBITED_OPERATIONS,
        }
        if (
            lpro_contract_payload["orchestrator_version"] != LPRO1_ORCHESTRATOR_VERSION
            or lpro_contract_payload["render_contract_version"]
            != LPRO1_RENDER_CONTRACT_VERSION
        ):
            raise ValidationFailureError("LPRO1_EXECUTION_CONTRACT_VERSION_MISMATCH")
        lpro_contract_hash = content_hash(lpro_contract_payload)
        lpro_contract = {
            "ref": (
                f"lpro1-execution-contract://{manifest['revision_id']}/"
                f"{lpro_contract_hash}"
            ),
            "content_hash": lpro_contract_hash,
            "contract": lpro_contract_payload,
        }
        exact_bindings["lpro1_execution_contract"] = deepcopy(lpro_contract)

        attempt_scope = {
            "provider_execution_plan": deepcopy(effective["provider_execution_plan"]),
            "stages": deepcopy(provider_plan["stages"]),
            "scene_routes": deepcopy(provider_plan["scene_routes"]),
            "one_route_per_scene": True,
            "single_run": True,
            "terminal_after_execution_begins": True,
            "automatic_retry_allowed": False,
            "provider_switch_allowed": False,
            "automatic_pexels_to_ai_fallback": False,
            "external_ai_video_fallback": False,
            "drive_idempotency_phases": deepcopy(
                next(
                    item.get("idempotency_phases") or []
                    for item in provider_plan["stages"]
                    if item.get("provider") == "google_drive"
                )
            ),
            "drive_phase_count": len(
                next(
                    item.get("idempotency_phases") or []
                    for item in provider_plan["stages"]
                    if item.get("provider") == "google_drive"
                )
            ),
            "drive_phases_are_distinct_authorized_mutations": bool(
                next(
                    item.get("idempotency_phases") or []
                    for item in provider_plan["stages"]
                    if item.get("provider") == "google_drive"
                )
            ),
            "hard_cost_ceiling": cost["hard_cap"],
            "currency": cost["currency"],
            "idempotency_fingerprint_contract": (
                "sha256(approval_content_hash,run_id,provider,operation,scene_id)"
            ),
        }
        cost_scope = {
            "cost_estimate_snapshot": deepcopy(effective["cost_estimate_snapshot"]),
            "currency": cost["currency"],
            "line_items": deepcopy(cost["line_items"]),
            "estimated_cost": cost["estimated_cost"],
            "hard_cap": cost["hard_cap"],
            "catalog_refs": deepcopy(cost["catalog_refs"]),
            "catalog_bindings": deepcopy(cost["catalog_bindings"]),
            "approval_amount": cost["hard_cap"],
            "actual_cost": None,
            "attempt_caps_bound": True,
        }
        destination = {
            **deepcopy(evidence["destination"]),
            "MR1_RENDER_DESTINATION_GATE": "PASS",
            "PUBLISH_DESTINATION_GATE": "BLOCKED_PENDING_PLATFORM_ID",
            "UPLOAD_READY": False,
            "PUBLISH_EXECUTION_READY": False,
            "publish_execution_authorized": False,
        }
        human_policy = {
            "technical_media_qc_pass_required": True,
            "creative_perceptual_media_qc_operator_acceptance_required": True,
            "exact_final_mp4_hash_review_required": True,
            "drive_archive_verified_required": True,
            "drive_finalization_supplement_verified_required": (
                project.project_type == SC04_PROJECT_TYPE
            ),
            "canonical_review_archive_remains_immutable": True,
            "finalization_supplement_before_final_media_ref_required": (
                project.project_type == SC04_PROJECT_TYPE
            ),
            "rights_and_provenance_complete_required": True,
            "pre_human_pass_media_authority": "REVIEW_MEDIA_CANDIDATE_ONLY",
            "final_media_ref_created": False,
            "final_media_ref_before_human_pass_allowed": False,
            "final_media_ref_before_drive_verified_allowed": False,
            "publish_approved": False,
        }
        exact_target = {
            "project_id": str(command.project_id),
            "project_ref": f"video-project://{command.project_id}",
            "project_type": project.project_type,
            "package_artifact_version_id": str(package.id),
            "package_artifact_version_ref": f"artifact-version://{package.id}",
            "package_content_hash": package.content_hash,
            "revision_id": manifest["revision_id"],
            "revision_version": manifest["revision_version"],
            "revision_hash": manifest["revision_hash"],
            "planning_output_set_hash": manifest["planning_output_set_hash"],
            "pkg1_approval_decision_id": str(command.pkg1_approval_decision_id),
            "pkg1_human_review_receipt_version_id": str(
                command.pkg1_human_review_receipt_version_id
            ),
            "pkg1_human_review_receipt_content_hash": evidence[
                "pkg1_receipt"
            ].content_hash,
            "reviewed_snapshot_hash": evidence["reviewed_snapshot_hash"],
        }
        return {
            "exact_target": exact_target,
            "exact_bindings": exact_bindings,
            "provider_attempt_scope": attempt_scope,
            "cost_scope": cost_scope,
            "destination": destination,
            "human_and_final_media_policy": human_policy,
            "lpro1_execution_contract": lpro_contract,
            "configuration_readiness": self._configuration_readiness(provider_plan),
            "upstream_entry": {
                "LPRO1_FINAL": "PASS",
                "GEO1_FINAL": "PASS",
                "GEO2_FINAL": "PASS",
                "CH1_MARKET_V3_FINAL": "PASS",
                "PKG1_MARKET_REVISION_FINAL": "PASS",
                "PKG1_SC04_REVISION_FINAL": (
                    "PASS"
                    if project.project_type == SC04_PROJECT_TYPE
                    else "NOT_APPLICABLE"
                ),
                "PRODUCTION_PACKAGE_APPROVED": True,
            },
        }

    def _validate_provider_plan(
        self,
        *,
        plan: dict[str, Any],
        revised: dict[str, Any],
        revision_id: str,
        revision_hash: str,
        source_plan: dict[str, Any] | None,
    ) -> None:
        if (
            plan.get("execution_enabled") is not False
            or plan.get("revision_id") != revision_id
            or plan.get("revision_hash") != revision_hash
            or plan.get("one_route_per_scene") is not True
            or plan.get("automatic_pexels_to_ai_fallback") is not False
            or plan.get("external_ai_video_fallback") is not False
            or plan.get("provider_outputs") != []
            or plan.get("visual_source_decision_set")
            != revised["visual_source_decision_set"]
            or plan.get("voice_policy") != revised["voice_policy"]
        ):
            raise ValidationFailureError("MR1_PROVIDER_PLAN_SCOPE_MISMATCH")
        stages = plan.get("stages") or []
        stage_by_provider = {
            item.get("provider"): item for item in stages if isinstance(item, dict)
        }
        required = {
            "elevenlabs",
            "forced_alignment",
            "pexels_api",
            "google_gemini_image",
            "google_veo",
            "native_graphics",
            "native_ffmpeg_renderer",
            "google_drive",
        }
        if set(stage_by_provider) != required:
            raise ValidationFailureError("MR1_PROVIDER_STAGE_SET_MISMATCH")
        drive_stage = stage_by_provider["google_drive"]
        if plan.get("schema_version") not in {
            "pkg1.market-provider-execution-plan.v1",
            "pkg1.sc04-provider-execution-plan.v1",
        } or (
            drive_stage.get("operation")
            != "canonical_review_archive_plus_finalization_supplement"
            or drive_stage.get("planned_requests") != 2
            or drive_stage.get("idempotency_phases") != DRIVE_IDEMPOTENCY_PHASES
        ):
            raise ValidationFailureError("MR1_DRIVE_MUTATION_PHASE_AUTHORITY_INVALID")
        for provider in (
            "elevenlabs",
            "forced_alignment",
            "pexels_api",
            "google_gemini_image",
            "google_veo",
        ):
            stage = stage_by_provider[provider]
            cap = stage.get("attempt_cap", stage.get("attempt_cap_per_scene"))
            if cap != 1:
                raise ValidationFailureError(
                    f"MR1_PROVIDER_ATTEMPT_CAP_INVALID:{provider}"
                )
        scene_routes = plan.get("scene_routes") or []
        if not scene_routes or len(
            {item.get("scene_id") for item in scene_routes}
        ) != len(scene_routes):
            raise ValidationFailureError("MR1_SCENE_ROUTE_SET_INVALID")
        is_sc04_revision = plan.get("schema_version") == (
            "pkg1.sc04-provider-execution-plan.v1"
        )
        source_routes_by_scene: dict[str, dict[str, Any]] = {}
        if is_sc04_revision:
            source_routes = (source_plan or {}).get("scene_routes") or []
            source_routes_by_scene = {
                str(item.get("scene_id")): item
                for item in source_routes
                if isinstance(item, dict)
            }
            if (
                not source_routes_by_scene
                or len(source_routes_by_scene) != len(source_routes)
                or set(source_routes_by_scene)
                != {str(item.get("scene_id")) for item in scene_routes}
            ):
                raise ValidationFailureError("MR1_SC04_SOURCE_ROUTE_SET_INVALID")
        for route in scene_routes:
            scene_id = str(route.get("scene_id") or "")
            current_revision_route = str(route.get("idempotency_ref") or "") == (
                f"provider-plan://{revision_id}/{scene_id}"
            )
            unchanged_source_route = (
                is_sc04_revision
                and scene_id != "SC-04"
                and route == source_routes_by_scene.get(scene_id)
            )
            route_authority_valid = (
                (
                    current_revision_route
                    if scene_id == "SC-04"
                    else unchanged_source_route
                )
                if is_sc04_revision
                else current_revision_route
            )
            if route.get("attempt_cap") not in {0, 1} or not route_authority_valid:
                raise ValidationFailureError("MR1_SCENE_ROUTE_ATTEMPT_INVALID")

    def _validate_cost_scope(
        self,
        *,
        cost: dict[str, Any],
        revised: dict[str, Any],
        reused: dict[str, Any],
        provider_plan: dict[str, Any],
    ) -> None:
        bindings = cost.get("bindings") or {}
        if (
            cost.get("currency") != "USD"
            or cost.get("actual_cost") is not None
            or cost.get("attempt_caps_bound") is not True
            or cost.get("decision") != "PASS"
            or not isinstance(cost.get("hard_cap"), (int, float))
            or cost.get("estimated_cost", float("inf")) > cost["hard_cap"]
            or bindings.get("script") != reused["script"]
            or bindings.get("scene_plan") != revised["visual_plan"]
            or bindings.get("provider_plan") != revised["provider_execution_plan"]
        ):
            raise ValidationFailureError("MR1_COST_SCOPE_STALE_OR_INVALID")
        catalog_bindings = cost.get("catalog_bindings") or []
        if not catalog_bindings:
            raise ValidationFailureError("MR1_COST_CATALOG_BINDINGS_MISSING")
        registry = ConfigRegistryService(self.session)
        for binding in catalog_bindings:
            record = registry.get_version(
                binding.get("catalog_key"), binding.get("catalog_version")
            )
            if (
                record is None
                or record.status != "active"
                or record.content_hash != binding.get("content_hash")
            ):
                raise ValidationFailureError("MR1_COST_CATALOG_BINDING_STALE")
        line_by_provider = {
            item.get("provider"): item for item in cost.get("line_items") or []
        }
        stages = {
            item.get("provider"): item for item in provider_plan.get("stages") or []
        }
        if set(stages) - set(line_by_provider) - {"native_graphics"}:
            raise ValidationFailureError("MR1_COST_PROVIDER_COVERAGE_INCOMPLETE")
        if (
            line_by_provider["pexels_api"].get("planned_scenes")
            != stages["pexels_api"].get("planned_requests")
            or line_by_provider["google_gemini_image"].get("planned_scenes")
            != stages["google_gemini_image"].get("planned_requests")
            or line_by_provider["google_veo"].get("planned_clips")
            != stages["google_veo"].get("planned_requests")
            or line_by_provider["google_drive"].get("planned_requests")
            != stages["google_drive"].get("planned_requests")
            or line_by_provider["google_drive"].get("planned_requests") != 2
            or line_by_provider["google_drive"].get("idempotency_phases")
            != DRIVE_IDEMPOTENCY_PHASES
            or line_by_provider["google_drive"].get(
                "estimated_incremental_cost_usd"
            )
            != 0.0
        ):
            raise ValidationFailureError("MR1_COST_OUTPUT_COUNT_MISMATCH")

    def _validate_market_rights(
        self,
        *,
        evidence: dict[str, Any],
        revised: dict[str, Any],
        artifact_authority: dict[str, Any],
    ) -> None:
        market = evidence["market_profile"]
        destination = evidence["destination"]
        if (
            market.get("primary_market") != "US"
            or market.get("primary_locale") != "en-US"
            or market.get("narration_locale") != "en-US"
            or market.get("primary_timezone") != "America/New_York"
            or market.get("currency") != "USD"
            or destination.get("platform") != "YOUTUBE"
            or destination.get("channel_handle") != "@SmallTeamAI"
            or destination.get("target_market") != "US"
            or destination.get("destination_status") != "PENDING_PLATFORM_ID"
            or destination.get("platform_channel_id") is not None
            or destination.get("credential_ref") is not None
            or destination.get("verification_state") == "VERIFIED"
        ):
            raise ValidationFailureError("MR1_MARKET_OR_DESTINATION_BINDING_INVALID")

        versions = artifact_authority["versions"]
        niche = versions["niche_alignment_dossier"].content or {}
        dossier = versions["market_alignment_dossier"].content or {}
        component_results = dossier.get("component_results") or []
        required_gates = {
            "research_jurisdiction_gate",
            "script_market_alignment_gate",
            "voice_locale_alignment_gate",
            "visual_market_alignment_gate",
            "thumbnail_market_alignment_gate",
            "metadata_market_alignment_gate",
        }
        sc04_variant = artifact_authority["variant"] == SC04_PROJECT_TYPE
        if sc04_variant:
            # The old dossiers remain immutable nonvisual evidence.  Their old
            # visual verdict is deliberately excluded and cannot satisfy MR1.
            required_gates.remove("visual_market_alignment_gate")
        passing_gates = {
            item.get("gate_key")
            for item in component_results
            if item.get("verdict") == "PASS"
        }
        rights = versions["rights_disclosure_completeness_report"].content or {}
        disclosure = versions["synthetic_media_disclosure_receipt_draft"].content or {}
        provenance = versions["asset_provenance_plan"].content or {}
        visual = versions["visual_direction_contract"].content or {}
        market_wrapper = versions["target_market_profile"].content or {}
        digest_wrapper = versions["target_market_digest"].content or {}
        destination_wrapper = versions["destination_binding"].content or {}
        supplemental_visual = artifact_authority.get("supplemental_visual_alignment")
        supplemental_content = (
            (supplemental_visual.content or {})
            if supplemental_visual is not None
            else {}
        )
        if (
            (not sc04_variant and niche.get("overall_verdict") != "PASS")
            or (not sc04_variant and dossier.get("overall_verdict") != "PASS")
            or not required_gates.issubset(passing_gates)
            or (
                sc04_variant
                and (
                    (supplemental_content.get("market_alignment") or {}).get("verdict")
                    != "PASS"
                    or (supplemental_content.get("niche_alignment") or {}).get(
                        "verdict"
                    )
                    != "PASS"
                    or supplemental_content.get("subject") != revised["visual_plan"]
                    or supplemental_content.get("all_required_checks_pass") is not True
                )
            )
            or rights.get("planning_state") != "PASS"
            or rights.get("decision") != "PASS"
            or rights.get("provider_outputs_claimed") is not False
            or disclosure.get("receipt_status") != "PRE_RENDER_PLANNED"
            or disclosure.get("provider_outputs_exist") is not False
            or provenance.get("provider_output_exists") is not False
            or visual.get("niche_visual_source_profile") != "STOCK_ASSISTED"
            or market_wrapper.get("canonical_hash")
            != evidence["market_profile"]["content_hash"]
            or digest_wrapper.get("canonical_hash")
            != evidence["market_digest"]["content_hash"]
            or destination_wrapper.get("canonical_hash")
            != evidence["destination"]["content_hash"]
        ):
            raise ValidationFailureError("MR1_MARKET_RIGHTS_EVIDENCE_NOT_EXACT_PASS")

    def _configuration_readiness(self, provider_plan: dict[str, Any]) -> dict[str, Any]:
        settings = Settings()
        stage_by_provider = {
            item.get("provider"): item for item in provider_plan.get("stages") or []
        }
        elevenlabs_key = self._secret_configured(settings.elevenlabs_api_key)
        pexels_key = self._secret_configured(settings.pexels_api_key)
        gemini_key = self._secret_configured(settings.gemini_api_key)
        drive_secret = self._secret_configured(
            settings.google_drive_oauth_client_secret
        )
        drive_credentials = bool(
            settings.google_drive_oauth_client_secrets_file
            or (settings.google_drive_oauth_client_id and drive_secret)
        )
        ffmpeg_ready = bool(shutil.which("ffmpeg"))
        ffprobe_ready = bool(shutil.which("ffprobe"))

        def required(provider: str) -> bool:
            return int(stage_by_provider[provider].get("planned_requests") or 0) > 0

        route_readiness = {
            "elevenlabs": elevenlabs_key,
            "forced_alignment": elevenlabs_key,
            "pexels_api": pexels_key,
            "google_gemini_image": (
                gemini_key if required("google_gemini_image") else True
            ),
            "google_veo": (gemini_key if required("google_veo") else True),
            "native_graphics": True,
            "native_ffmpeg_renderer": ffmpeg_ready and ffprobe_ready,
            "google_drive": bool(
                settings.google_drive_root_folder_id and drive_credentials
            ),
        }
        return {
            "provider_credential_configured": {
                "elevenlabs": elevenlabs_key,
                "pexels_api": pexels_key,
                "google_gemini": gemini_key,
                "google_drive": drive_credentials,
            },
            "provider_route_readiness": route_readiness,
            "renderer_toolchain_readiness": {
                "ffmpeg": ffmpeg_ready,
                "ffprobe": ffprobe_ready,
                "ready": ffmpeg_ready and ffprobe_ready,
            },
            "drive_readiness": {
                "root_folder_configured": bool(settings.google_drive_root_folder_id),
                "credential_configured": drive_credentials,
                "ready": route_readiness["google_drive"],
            },
            "activation_switches_observed_without_mutation": {
                "media_provider_calls_disabled": (
                    settings.media_provider_calls_disabled
                ),
                "upload_and_publish_disabled": (settings.upload_and_publish_disabled),
                "provider_real_execution_enabled": (
                    settings.provider_real_execution_enabled
                ),
                "native_ffmpeg_production_enabled": (
                    settings.native_ffmpeg_production_enabled
                ),
                "google_drive_real_archive_enabled": (
                    settings.google_drive_real_archive_enabled
                ),
            },
            "provider_calls": 0,
            "render_calls": 0,
            "drive_calls": 0,
            "youtube_calls": 0,
        }

    @staticmethod
    def _secret_configured(value: Any) -> bool:
        if value is None:
            return False
        getter = getattr(value, "get_secret_value", None)
        raw = getter() if callable(getter) else value
        return bool(str(raw).strip())

    @staticmethod
    def _approval_ref(
        *,
        command: MR1ReapprovalCommand,
        evidence: dict[str, Any],
    ) -> str:
        return (
            f"mr1-approval://{command.channel_key}/{command.project_id}/"
            f"{evidence['package'].content['revision_id']}/"
            f"v{command.approval_version}"
        )

    def _reuse_decision_content(
        self,
        *,
        evidence: dict[str, Any],
        derived: dict[str, Any],
    ) -> dict[str, Any]:
        """Classify old outputs without granting any implicit reuse authority."""

        package: ArtifactVersion = evidence["package"]
        project: VideoProject = evidence["project"]
        manifest = package.content or {}
        authority = manifest.get("effective_artifact_authority") or {}
        source_project_id = (authority.get("authority_project_ids") or {}).get(
            "source_revision"
        )
        try:
            source_id = uuid.UUID(str(source_project_id))
        except (TypeError, ValueError) as exc:
            raise ValidationFailureError(
                "MR1_REUSE_SOURCE_PROJECT_REF_INVALID"
            ) from exc
        run_artifacts = list(
            self.session.scalars(
                select(Artifact).where(
                    Artifact.video_project_id == source_id,
                    Artifact.artifact_type == "mr1_execution_run",
                )
            ).all()
        )
        if len(run_artifacts) != 1 or run_artifacts[0].current_version_id is None:
            raise ValidationFailureError("MR1_REUSE_EXACT_SOURCE_RUN_REQUIRED")
        source_run = self.session.get(
            ArtifactVersion, run_artifacts[0].current_version_id
        )
        if (
            source_run is None
            or content_hash(source_run.content or {}) != source_run.content_hash
        ):
            raise ValidationFailureError("MR1_REUSE_SOURCE_RUN_HASH_INVALID")
        state = source_run.content or {}
        provider_outputs = state.get("provider_outputs") or {}
        attempts = state.get("attempts") or {}
        scene_executions = state.get("scene_executions") or {}
        resolved = self.resolve_package_artifact_authority(
            project=project,
            package=package,
        )
        effective = resolved["refs"]
        spoken = resolved["versions"]["spoken_text_normalized"].content or {}
        voice = resolved["versions"]["voice_policy"].content or {}
        provider_plan = resolved["versions"]["provider_execution_plan"].content or {}
        source_package_ref = manifest.get("supersedes") or {}
        source_package = self._version_from_ref(
            source_package_ref,
            "package_manifest",
            allowed_project_ids={source_id},
        )
        source_project = self.session.get(VideoProject, source_id)
        if source_project is None:
            raise ValidationFailureError("MR1_REUSE_SOURCE_PROJECT_MISSING")
        source_resolved = self.resolve_package_artifact_authority(
            project=source_project,
            package=source_package,
        )
        source_effective = source_resolved["refs"]
        if (
            state.get("project_id") != str(source_project.id)
            or state.get("package_artifact_version_id") != str(source_package.id)
            or state.get("package_content_hash") != source_package.content_hash
        ):
            raise ValidationFailureError("MR1_REUSE_SOURCE_RUN_PACKAGE_MISMATCH")

        unchanged_output_authorities = {
            key: bool(source_effective.get(key) == effective.get(key))
            for key in (
                "script",
                "spoken_text_normalized",
                "narration_pacing_preflight_estimate",
                "voice_policy",
                "synthetic_media_disclosure_receipt_draft",
            )
        }
        old_rights = source_resolved["versions"][
            "rights_disclosure_completeness_report"
        ]
        old_provenance = source_resolved["versions"]["asset_provenance_plan"]
        new_rights = resolved["versions"]["rights_disclosure_completeness_report"]
        new_provenance = resolved["versions"]["asset_provenance_plan"]
        disclosure = (
            resolved["versions"]["synthetic_media_disclosure_receipt_draft"].content
            or {}
        )
        old_rights_content = old_rights.content or {}
        new_rights_content = new_rights.content or {}
        old_provenance_content = old_provenance.content or {}
        new_provenance_content = new_provenance.content or {}
        rights_planning_compatible = bool(
            all(unchanged_output_authorities.values())
            and new_rights_content.get("supersedes")
            == PKG1MarketRevisionService._version_ref(old_rights)
            and new_provenance_content.get("supersedes")
            == PKG1MarketRevisionService._version_ref(old_provenance)
            and all(
                item.get("planning_state") == "PASS"
                and item.get("decision") == "PASS"
                and item.get("provider_outputs_claimed") is False
                and item.get("generated_evidence_authority") is False
                for item in (old_rights_content, new_rights_content)
            )
            and all(
                item.get("provider_output_exists") is False
                and item.get("generated_evidence_authority") is False
                for item in (old_provenance_content, new_provenance_content)
            )
            and disclosure.get("receipt_status") == "PRE_RENDER_PLANNED"
            and disclosure.get("provider_outputs_exist") is False
            and disclosure.get("synthetic_voice_planned") is True
        )

        try:
            source_approval = self.session.get(
                ApprovalDecision, uuid.UUID(str(state.get("approval_id")))
            )
        except (TypeError, ValueError):
            source_approval = None
        source_approval_historical_only = bool(
            source_approval is not None
            and source_approval.decision == "approved"
            and source_approval.target_artifact_version_id == source_package.id
            and (source_approval.metadata_ or {}).get("package_artifact_version_id")
            == str(source_package.id)
            and (source_approval.metadata_ or {}).get("package_content_hash")
            == source_package.content_hash
        )

        def file_sha256(path: Path) -> str:
            digest = hashlib.sha256()
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            return digest.hexdigest()

        workspace_raw = state.get("workspace")
        source_workspace = (
            Path(str(workspace_raw)).resolve()
            if isinstance(workspace_raw, str) and workspace_raw
            else None
        )

        def exact_source_file(
            relative: str,
            *,
            declared_path: Any = None,
        ) -> tuple[Path | None, list[str]]:
            failures: list[str] = []
            if (
                source_workspace is None
                or not source_workspace.is_dir()
                or source_workspace.is_symlink()
            ):
                return None, ["SOURCE_WORKSPACE_INVALID"]
            candidate = source_workspace / relative
            try:
                resolved_path = candidate.resolve(strict=True)
            except OSError:
                return None, ["SOURCE_FILE_MISSING"]
            if (
                candidate.is_symlink()
                or not resolved_path.is_file()
                or (
                    resolved_path != source_workspace
                    and source_workspace not in resolved_path.parents
                )
            ):
                failures.append("SOURCE_FILE_CONTAINMENT_INVALID")
            if declared_path:
                try:
                    declared = Path(str(declared_path)).resolve(strict=True)
                except OSError:
                    failures.append("DECLARED_SOURCE_PATH_INVALID")
                else:
                    if declared != resolved_path:
                        failures.append("DECLARED_SOURCE_PATH_MISMATCH")
            return resolved_path, failures

        def exact_json_file(
            relative: str,
            *,
            expected: dict[str, Any],
        ) -> tuple[dict[str, Any], list[str]]:
            path, failures = exact_source_file(relative)
            proof: dict[str, Any] = {
                "source_path": str(path) if path is not None else None,
                "file_sha256": None,
                "size_bytes": None,
                "provider_output_content_hash": content_hash(expected),
                "json_exact": False,
            }
            if path is None or failures:
                return proof, failures
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return proof, ["SOURCE_JSON_INVALID"]
            proof.update(
                {
                    "file_sha256": file_sha256(path),
                    "size_bytes": path.stat().st_size,
                    "json_exact": payload == expected,
                }
            )
            if payload != expected:
                failures.append("SOURCE_PROVIDER_OUTPUT_JSON_MISMATCH")
            return proof, failures

        def attempt_proof(operation_key: str) -> tuple[dict[str, Any], list[str]]:
            ledger = attempts.get(operation_key) or {}
            proof: dict[str, Any] = {
                "operation_key": operation_key,
                "source_attempt_state": ledger.get("state"),
                "source_attempt_count": ledger.get("attempt_count"),
                "source_network_submit_started": ledger.get("network_submit_started"),
                "source_request_hash": ledger.get("request_hash"),
                "artifact_version_id": None,
                "artifact_content_hash": None,
                "artifact_hash_revalidated": False,
                "historical_only_not_reused": True,
            }
            failures: list[str] = []
            artifact_ids = state.get("attempt_artifact_ids") or {}
            raw_artifact_id = artifact_ids.get(operation_key)
            try:
                artifact = self.session.get(Artifact, uuid.UUID(str(raw_artifact_id)))
            except (TypeError, ValueError):
                artifact = None
            version = (
                self.session.get(ArtifactVersion, artifact.current_version_id)
                if artifact is not None and artifact.current_version_id is not None
                else None
            )
            persisted = version.content if version is not None else {}
            if (
                artifact is None
                or version is None
                or artifact.video_project_id != source_project.id
                or artifact.artifact_type != "mr1_provider_attempt_ledger"
                or content_hash(version.content or {}) != version.content_hash
                or persisted.get("operation_key") != operation_key
                or persisted.get("state") != ledger.get("state")
                or persisted.get("attempt_count") != ledger.get("attempt_count")
                or persisted.get("request_hash") != ledger.get("request_hash")
            ):
                failures.append("SOURCE_ATTEMPT_LEDGER_INVALID")
            else:
                proof.update(
                    {
                        "artifact_version_id": str(version.id),
                        "artifact_content_hash": version.content_hash,
                        "artifact_hash_revalidated": True,
                    }
                )
            if (
                ledger.get("state") != "SUCCEEDED"
                or ledger.get("attempt_count") != 1
                or ledger.get("network_submit_started") is not True
                or not isinstance(ledger.get("request_hash"), str)
                or len(ledger["request_hash"]) != 64
            ):
                failures.append("SOURCE_ATTEMPT_NOT_EXACT_SUCCESS")
            return proof, failures

        rights_proof = {
            "rights_disclosure": deepcopy(
                effective["rights_disclosure_completeness_report"]
            ),
            "synthetic_disclosure": deepcopy(
                effective["synthetic_media_disclosure_receipt_draft"]
            ),
            "asset_provenance": deepcopy(effective["asset_provenance_plan"]),
            "output_specific_rights_receipt_present": False,
            "planning_compatibility": rights_planning_compatible,
            "source_rights": PKG1MarketRevisionService._version_ref(old_rights),
            "source_provenance": PKG1MarketRevisionService._version_ref(old_provenance),
            "fresh_rights_supersedes_source": bool(
                new_rights_content.get("supersedes")
                == PKG1MarketRevisionService._version_ref(old_rights)
            ),
            "fresh_provenance_supersedes_source": bool(
                new_provenance_content.get("supersedes")
                == PKG1MarketRevisionService._version_ref(old_provenance)
            ),
        }

        entries: list[dict[str, Any]] = []

        def add_entry(
            *,
            output_key: str,
            classification: str,
            prior_output: Any,
            request_proof: dict[str, Any],
            script_proof: dict[str, Any],
            provider_settings_proof: dict[str, Any],
            checksum_proof: dict[str, Any],
            qc_proof: dict[str, Any],
            reason_codes: list[str],
        ) -> None:
            if classification not in {
                "REUSE_VALID",
                "INVALIDATED_BY_REVISION",
                "MISSING",
                "REQUIRES_NEW_EXECUTION",
            }:
                raise ValidationFailureError("MR1_REUSE_CLASSIFICATION_INVALID")
            entries.append(
                {
                    "output_key": output_key,
                    "classification": classification,
                    "prior_output_present": bool(prior_output),
                    "prior_output_evidence": deepcopy(prior_output),
                    "request_identity_proof": request_proof,
                    "script_binding_proof": script_proof,
                    "provider_model_settings_proof": provider_settings_proof,
                    "checksum_proof": checksum_proof,
                    "rights_proof": deepcopy(rights_proof),
                    "qc_proof": qc_proof,
                    "reuse_authorized": classification == "REUSE_VALID",
                    "reason_codes": reason_codes,
                }
            )

        narration = provider_outputs.get("narration") or {}
        narration_attempt = attempts.get("elevenlabs:narration") or {}
        narration_present = bool(narration)
        voice_identity = voice.get("voice_identity") or {}
        voice_settings = (voice.get("pacing_policy") or {}).get("settings") or {}
        narration_attempt_proof, narration_failures = attempt_proof(
            "elevenlabs:narration"
        )
        narration_json_proof, narration_json_failures = exact_json_file(
            "provider_evidence/narration-output.json",
            expected=narration,
        )
        narration_failures.extend(narration_json_failures)
        audio_path, audio_path_failures = exact_source_file(
            "narration/narration.mp3",
            declared_path=narration.get("audio_path") or narration.get("output_path"),
        )
        narration_failures.extend(audio_path_failures)
        actual_audio_sha256: str | None = None
        actual_audio_size: int | None = None
        if audio_path is not None and not audio_path_failures:
            actual_audio_sha256 = file_sha256(audio_path)
            actual_audio_size = audio_path.stat().st_size
            if actual_audio_sha256 != narration.get("audio_sha256"):
                narration_failures.append("NARRATION_AUDIO_SHA256_MISMATCH")
            if actual_audio_size != narration.get("audio_size_bytes"):
                narration_failures.append("NARRATION_AUDIO_SIZE_MISMATCH")
        runtime_gate = state.get("narration_runtime_gate") or {}
        runtime_gate_hash = runtime_gate.get("content_hash")
        runtime_gate_core = {
            key: deepcopy(value)
            for key, value in runtime_gate.items()
            if key != "content_hash"
        }
        pacing_ref = effective["narration_pacing_preflight_estimate"]
        timing_seed = narration.get("timing_seed") or {}
        timing_seed_hash = timing_seed.get("content_hash")
        timing_seed_core = {
            key: deepcopy(value)
            for key, value in timing_seed.items()
            if key != "content_hash"
        }
        narration_request_exact = bool(
            narration_attempt.get("request_hash")
            and narration_attempt.get("request_hash") == narration.get("request_hash")
            and len(str(narration.get("request_hash"))) == 64
        )
        narration_script_exact = bool(
            all(unchanged_output_authorities.values())
            and narration.get("spoken_text_artifact_version_id")
            == effective["spoken_text_normalized"]["artifact_version_id"]
            and narration.get("normalized_text_hash")
            == spoken.get("normalized_text_hash")
        )
        narration_settings_exact = bool(
            narration.get("provider") == "elevenlabs"
            and narration.get("operation") == "narration"
            and narration.get("voice_id") == voice_identity.get("voice_id")
            and narration.get("model_id") == voice_identity.get("model_id")
            and narration.get("voice_settings") == voice_settings
            and narration.get("provider_call_made") is True
            and narration.get("network_submit_count") == 1
            and narration.get("sdk_retry") is False
            and narration.get("secret_values_exposed") is False
        )
        narration_runtime_qc_exact = bool(
            runtime_gate.get("result") == "PASS"
            and not runtime_gate.get("reason_codes")
            and runtime_gate_hash == content_hash(runtime_gate_core)
            and runtime_gate.get("actual_duration_ms")
            == narration.get("audio_duration_ms")
            and runtime_gate.get("pacing_artifact_version_id")
            == pacing_ref["artifact_version_id"]
            and runtime_gate.get("pacing_artifact_content_hash")
            == pacing_ref["content_hash"]
            and int(runtime_gate.get("minimum_duration_ms") or 0)
            <= int(narration.get("audio_duration_ms") or -1)
            <= int(runtime_gate.get("maximum_duration_ms") or 0)
            and timing_seed_hash == content_hash(timing_seed_core)
            and timing_seed.get("timing_available") is True
            and timing_seed.get("audio_asset_ref") == narration.get("audio_asset_ref")
            and timing_seed.get("audio_duration_ms")
            == narration.get("audio_duration_ms")
        )
        if not narration_present:
            narration_failures.append("PRIOR_NARRATION_OUTPUT_MISSING")
        if not narration_request_exact:
            narration_failures.append("NARRATION_REQUEST_LEDGER_MISMATCH")
        if not narration_script_exact:
            narration_failures.append("NARRATION_SCRIPT_BINDING_CHANGED")
        if not narration_settings_exact:
            narration_failures.append("NARRATION_PROVIDER_SETTINGS_MISMATCH")
        if not narration_runtime_qc_exact:
            narration_failures.append("NARRATION_PRIOR_QC_NOT_EXACT_PASS")
        if not rights_planning_compatible:
            narration_failures.append("NARRATION_RIGHTS_PLANNING_INCOMPATIBLE")
        if not source_approval_historical_only:
            narration_failures.append("SOURCE_APPROVAL_LINEAGE_INVALID")
        narration_failures = sorted(set(narration_failures))
        narration_valid = narration_present and not narration_failures
        add_entry(
            output_key="narration_audio",
            classification=(
                "REUSE_VALID"
                if narration_valid
                else "REQUIRES_NEW_EXECUTION"
                if narration_present
                else "MISSING"
            ),
            prior_output={
                "provider_output_path": narration_json_proof.get("source_path"),
                "provider_output_file_sha256": narration_json_proof.get("file_sha256"),
                "provider_output_content_hash": narration_json_proof.get(
                    "provider_output_content_hash"
                ),
                "audio_path": str(audio_path) if audio_path is not None else None,
                "audio_sha256": narration.get("audio_sha256"),
                "audio_size_bytes": narration.get("audio_size_bytes"),
                "audio_duration_ms": narration.get("audio_duration_ms"),
                "audio_asset_ref": narration.get("audio_asset_ref"),
                "source_run_id": state.get("run_id"),
            }
            if narration_present
            else {},
            request_proof={
                "prior_request_hash": narration_attempt.get("request_hash"),
                "output_request_hash": narration.get("request_hash"),
                "prior_internal_match": narration_request_exact,
                "source_attempt_ledger": narration_attempt_proof,
                "fresh_provider_request_required": False,
                "fresh_request_hash": None,
                "fresh_run_identity_required": True,
                "old_attempt_ledger_reusable": False,
                "exact_request_match": narration_request_exact,
            },
            script_proof={
                "effective_script": deepcopy(effective["script"]),
                "effective_spoken_text": deepcopy(effective["spoken_text_normalized"]),
                "prior_normalized_text_hash": narration.get("normalized_text_hash"),
                "effective_normalized_text_hash": spoken.get("normalized_text_hash"),
                "normalized_text_match": bool(
                    narration.get("normalized_text_hash")
                    and narration.get("normalized_text_hash")
                    == spoken.get("normalized_text_hash")
                ),
                "source_and_target_artifact_refs_unchanged": deepcopy(
                    unchanged_output_authorities
                ),
                "exact_output_authority_match": narration_script_exact,
            },
            provider_settings_proof={
                "prior_provider": narration.get("provider"),
                "prior_voice_id": narration.get("voice_id"),
                "prior_model_id": narration.get("model_id"),
                "effective_voice_id": voice_identity.get("voice_id"),
                "effective_model_id": voice_identity.get("model_id"),
                "settings_exact": bool(
                    narration.get("voice_id") == voice_identity.get("voice_id")
                    and narration.get("model_id") == voice_identity.get("model_id")
                    and narration.get("voice_settings") == voice_settings
                ),
                "effective_voice_settings": deepcopy(voice_settings),
            },
            checksum_proof={
                "declared_sha256": narration.get("audio_sha256"),
                "declared_size_bytes": narration.get("audio_size_bytes"),
                "actual_sha256": actual_audio_sha256,
                "actual_size_bytes": actual_audio_size,
                "source_path": str(audio_path) if audio_path is not None else None,
                "provider_output_json": narration_json_proof,
                "actual_bytes_rehashed_for_revision": actual_audio_sha256 is not None,
                "checksum_exact": bool(
                    actual_audio_sha256
                    and actual_audio_sha256 == narration.get("audio_sha256")
                    and actual_audio_size == narration.get("audio_size_bytes")
                    and narration_json_proof.get("json_exact") is True
                ),
            },
            qc_proof={
                "source_narration_runtime_gate": deepcopy(runtime_gate),
                "runtime_gate_content_hash_revalidated": bool(
                    runtime_gate_hash == content_hash(runtime_gate_core)
                ),
                "timing_seed_content_hash_revalidated": bool(
                    timing_seed_hash == content_hash(timing_seed_core)
                ),
                "output_specific_technical_qc": narration_runtime_qc_exact,
                "output_specific_creative_qc": False,
                "exact_revision_qc": narration_runtime_qc_exact,
            },
            reason_codes=(
                [
                    "IMMUTABLE_AUDIO_COMPLETE_PROOF_PASS",
                    "FRESH_APPROVAL_AUTHORIZES_OUTPUT_REUSE_ONLY",
                    "OLD_APPROVAL_RUN_AND_ATTEMPT_LEDGERS_REMAIN_HISTORICAL",
                ]
                if narration_valid
                else narration_failures
            ),
        )

        alignment = (
            provider_outputs.get("forced_alignment")
            or provider_outputs.get("alignment")
            or {}
        )
        alignment_attempt = attempts.get("elevenlabs:forced_alignment") or {}
        alignment_present = bool(alignment)
        alignment_attempt_proof, alignment_failures = attempt_proof(
            "elevenlabs:forced_alignment"
        )
        alignment_json_proof, alignment_json_failures = exact_json_file(
            "alignment/alignment.json",
            expected=alignment,
        )
        alignment_failures.extend(alignment_json_failures)
        provider_alignment_proof, provider_alignment_failures = exact_json_file(
            "provider_evidence/alignment-output.json",
            expected=alignment,
        )
        alignment_failures.extend(provider_alignment_failures)
        forced = alignment.get("forced_alignment_evidence") or {}
        forced_hash = forced.get("content_hash")
        forced_core = {
            key: deepcopy(value)
            for key, value in forced.items()
            if key != "content_hash"
        }
        temporal_normalized = alignment.get("temporal_spoken_text_normalized") or {}
        temporal_normalized_hash = temporal_normalized.get("content_hash")
        temporal_normalized_core = {
            key: deepcopy(value)
            for key, value in temporal_normalized.items()
            if key != "content_hash"
        }
        temporal = state.get("temporal_authority") or {}
        temporal_gate_path, temporal_gate_failures = exact_source_file(
            "temporal/temporal-authority-gate.json"
        )
        alignment_failures.extend(temporal_gate_failures)
        temporal_gate: dict[str, Any] = {}
        temporal_gate_file_sha256: str | None = None
        if temporal_gate_path is not None and not temporal_gate_failures:
            try:
                temporal_gate = json.loads(
                    temporal_gate_path.read_text(encoding="utf-8")
                )
            except (OSError, json.JSONDecodeError):
                alignment_failures.append("SOURCE_TEMPORAL_GATE_JSON_INVALID")
            else:
                temporal_gate_file_sha256 = file_sha256(temporal_gate_path)
        alignment_request_exact = bool(
            alignment_attempt.get("request_hash")
            and alignment_attempt.get("request_hash") == alignment.get("request_hash")
            and len(str(alignment.get("request_hash"))) == 64
        )
        alignment_script_exact = bool(
            all(unchanged_output_authorities.values())
            and alignment.get("normalized_text_hash")
            == spoken.get("normalized_text_hash")
            and alignment.get("spoken_text_hash")
            == effective["spoken_text_normalized"]["content_hash"]
            and temporal_normalized_hash == content_hash(temporal_normalized_core)
            and temporal_normalized.get("spoken_text") == spoken.get("normalized_text")
        )
        alignment_evidence_exact = bool(
            alignment.get("provider") == "forced_alignment"
            and alignment.get("operation") == "forced_alignment"
            and alignment.get("provider_call_made") is True
            and alignment.get("network_submit_count") == 1
            and alignment.get("sdk_retry") is False
            and alignment.get("secret_values_exposed") is False
            and alignment.get("verification_status") == "PASS"
            and float(alignment.get("token_coverage") or 0) == 1.0
            and alignment.get("missing_tokens") == []
            and alignment.get("extra_tokens") == []
            and alignment.get("estimated_timing_fallback_used") is False
            and alignment.get("audio_sha256") == actual_audio_sha256
            and alignment.get("audio_duration_ms") == narration.get("audio_duration_ms")
            and forced_hash == content_hash(forced_core)
            and forced_hash == alignment.get("forced_alignment_content_hash")
            and alignment.get("forced_alignment_ref")
            == f"forced-alignment:{forced_hash}"
            and forced.get("verification_status") == "PASS"
            and forced.get("missing_tokens") == []
            and forced.get("extra_words") == []
            and forced.get("spoken_text_hash")
            == temporal_normalized.get("spoken_text_hash")
            and forced.get("audio_asset_ref") == narration.get("audio_asset_ref")
            and forced.get("audio_duration_ms") == narration.get("audio_duration_ms")
        )
        temporal_qc_exact = bool(
            temporal.get("result") == "PASS"
            and temporal.get("state") == "CANONICAL_TIMELINE_READY"
            and temporal.get("timing_authority") == "CANONICAL_MEDIA_TIMELINE"
            and float(temporal.get("token_coverage") or 0) == 1.0
            and temporal.get("estimated_timing_fallback_used") is False
            and temporal.get("audio_asset_ref") == narration.get("audio_asset_ref")
            and temporal.get("audio_duration_ms") == narration.get("audio_duration_ms")
            and temporal_gate.get("gate_status") == "PASS"
            and temporal_gate.get("block_reasons") == []
            and temporal_gate.get("content_hash")
            == content_hash(
                {
                    key: deepcopy(value)
                    for key, value in temporal_gate.items()
                    if key != "content_hash"
                }
            )
            and temporal_gate.get("content_hash") == temporal.get("temporal_gate_hash")
        )
        if not alignment_present:
            alignment_failures.append("PRIOR_FORCED_ALIGNMENT_OUTPUT_MISSING")
        if not narration_valid:
            alignment_failures.append("REUSABLE_NARRATION_PREREQUISITE_FAILED")
        if not alignment_request_exact:
            alignment_failures.append("ALIGNMENT_REQUEST_LEDGER_MISMATCH")
        if not alignment_script_exact:
            alignment_failures.append("ALIGNMENT_SPOKEN_TEXT_BINDING_CHANGED")
        if not alignment_evidence_exact:
            alignment_failures.append("FORCED_ALIGNMENT_EVIDENCE_INVALID")
        if not temporal_qc_exact:
            alignment_failures.append("PRIOR_TEMPORAL_VERIFICATION_NOT_PASS")
        if not rights_planning_compatible:
            alignment_failures.append("ALIGNMENT_RIGHTS_PLANNING_INCOMPATIBLE")
        alignment_failures = sorted(set(alignment_failures))
        alignment_valid = alignment_present and not alignment_failures
        add_entry(
            output_key="forced_alignment",
            classification=(
                "REUSE_VALID"
                if alignment_valid
                else "REQUIRES_NEW_EXECUTION"
                if alignment_present
                else "MISSING"
            ),
            prior_output={
                "alignment_path": alignment_json_proof.get("source_path"),
                "alignment_file_sha256": alignment_json_proof.get("file_sha256"),
                "provider_output_path": provider_alignment_proof.get("source_path"),
                "provider_output_file_sha256": provider_alignment_proof.get(
                    "file_sha256"
                ),
                "provider_output_content_hash": alignment_json_proof.get(
                    "provider_output_content_hash"
                ),
                "forced_alignment_content_hash": alignment.get(
                    "forced_alignment_content_hash"
                ),
                "audio_sha256": alignment.get("audio_sha256"),
                "audio_duration_ms": alignment.get("audio_duration_ms"),
                "source_run_id": state.get("run_id"),
            }
            if alignment_present
            else {},
            request_proof={
                "prior_request_hash": alignment_attempt.get("request_hash"),
                "output_request_hash": alignment.get("request_hash"),
                "prior_internal_match": alignment_request_exact,
                "source_attempt_ledger": alignment_attempt_proof,
                "fresh_provider_request_required": False,
                "fresh_request_hash": None,
                "fresh_run_identity_required": True,
                "old_attempt_ledger_reusable": False,
                "exact_request_match": alignment_request_exact,
            },
            script_proof={
                "effective_spoken_text": deepcopy(effective["spoken_text_normalized"]),
                "prior_spoken_text_hash": alignment.get("spoken_text_hash"),
                "effective_spoken_text_hash": effective["spoken_text_normalized"][
                    "content_hash"
                ],
                "spoken_text_match": bool(
                    alignment.get("spoken_text_hash")
                    and alignment.get("spoken_text_hash")
                    == effective["spoken_text_normalized"]["content_hash"]
                ),
                "source_and_target_artifact_refs_unchanged": deepcopy(
                    unchanged_output_authorities
                ),
            },
            provider_settings_proof={
                "prior_provider": alignment.get("provider"),
                "effective_provider": "forced_alignment",
                "settings_exact": alignment.get("provider") == "forced_alignment"
                and narration.get("voice_id") == voice_identity.get("voice_id")
                and narration.get("model_id") == voice_identity.get("model_id")
                and narration.get("voice_settings") == voice_settings,
            },
            checksum_proof={
                "audio_sha256": alignment.get("audio_sha256"),
                "alignment_content_hash": alignment.get(
                    "forced_alignment_content_hash"
                ),
                "alignment_json": alignment_json_proof,
                "provider_output_json": provider_alignment_proof,
                "actual_alignment_bytes_rehashed_for_revision": bool(
                    alignment_json_proof.get("file_sha256")
                ),
                "checksum_exact": bool(
                    alignment_json_proof.get("json_exact") is True
                    and provider_alignment_proof.get("json_exact") is True
                    and alignment_json_proof.get("file_sha256")
                    == provider_alignment_proof.get("file_sha256")
                    and alignment_evidence_exact
                ),
            },
            qc_proof={
                "prior_verification_status": alignment.get("verification_status"),
                "token_coverage": alignment.get("token_coverage"),
                "prior_temporal_gate": {
                    "source_path": str(temporal_gate_path)
                    if temporal_gate_path is not None
                    else None,
                    "file_sha256": temporal_gate_file_sha256,
                    "content": deepcopy(temporal_gate),
                    "historical_verification_only": True,
                    "reuse_authorized": False,
                },
                "alignment_evidence_hash_revalidated": alignment_evidence_exact,
                "exact_revision_qc": temporal_qc_exact,
            },
            reason_codes=(
                [
                    "IMMUTABLE_FORCED_ALIGNMENT_COMPLETE_PROOF_PASS",
                    "FRESH_APPROVAL_AUTHORIZES_OUTPUT_REUSE_ONLY",
                    "OLD_TEMPORAL_AUTHORITY_REMAINS_HISTORICAL",
                ]
                if alignment_valid
                else alignment_failures
            ),
        )

        add_entry(
            output_key="canonical_timeline_and_captions",
            classification="REQUIRES_NEW_EXECUTION",
            prior_output={
                "source_run_id": state.get("run_id"),
                "prior_timeline_hash": temporal.get("timeline_hash"),
                "prior_verified_alignment_hash": temporal.get(
                    "verified_alignment_hash"
                ),
                "prior_temporal_gate_hash": temporal.get("temporal_gate_hash"),
                "historical_verification_status": (
                    "PASS" if temporal_qc_exact else "NOT_PASS"
                ),
            },
            request_proof={
                "fresh_run_identity_required": True,
                "fresh_temporal_compilation_required": True,
                "exact_request_match": False,
            },
            script_proof={
                "effective_script": deepcopy(effective["script"]),
                "effective_spoken_text": deepcopy(effective["spoken_text_normalized"]),
                "fresh_binding_required": True,
            },
            provider_settings_proof={
                "provider_call_required": False,
                "deterministic_local_compilation": True,
                "settings_exact": False,
            },
            checksum_proof={
                "prior_temporal_bytes_reusable": False,
                "fresh_output_checksums_required": True,
                "checksum_exact": False,
            },
            qc_proof={
                "prior_temporal_gate_used_as_alignment_qc_only": temporal_qc_exact,
                "fresh_sc04_temporal_gate_required": True,
                "exact_revision_qc": False,
            },
            reason_codes=[
                "OLD_CANONICAL_TIMELINE_AND_VISUAL_SUBWINDOWS_NONREUSABLE",
                "FRESH_SC04_TEMPORAL_COMPILATION_REQUIRED",
                "FRESH_CAPTION_COMPILATION_REQUIRED",
            ],
        )

        route_by_scene = {
            item.get("scene_id"): item.get("route")
            for item in provider_plan.get("scene_routes") or []
        }
        for scene_id in tuple(f"SC-{index:02d}" for index in range(1, 10)):
            prior_scene = scene_executions.get(scene_id) or {}
            scene_attempts = [
                deepcopy(item)
                for item in attempts.values()
                if isinstance(item, dict) and item.get("scene_id") == scene_id
            ]
            if scene_id == "SC-04":
                classification = "INVALIDATED_BY_REVISION"
                reasons = [
                    "SC04_ROUTE_CHANGED_PEXELS_TO_NATIVE_MOTION_GRAPHIC",
                    "OLD_PEXELS_ATTEMPTS_ARE_NONREUSABLE_EVIDENCE",
                ]
            elif prior_scene:
                classification = "REQUIRES_NEW_EXECUTION"
                reasons = [
                    "PRIOR_SCENE_OUTPUT_LACKS_EXACT_REVISION_CHECKSUM_RIGHTS_QC_PROOF"
                ]
            else:
                classification = "MISSING"
                reasons = ["PRIOR_SCENE_OUTPUT_MISSING"]
            add_entry(
                output_key=f"scene:{scene_id}",
                classification=classification,
                prior_output={
                    "scene_execution": deepcopy(prior_scene),
                    "attempts": scene_attempts,
                }
                if prior_scene or scene_attempts
                else {},
                request_proof={
                    "effective_route": route_by_scene.get(scene_id),
                    "prior_attempt_request_hashes": [
                        item.get("request_hash") for item in scene_attempts
                    ],
                    "fresh_target_request_hash": None,
                    "exact_request_match": False,
                },
                script_proof={
                    "effective_script": deepcopy(effective["script"]),
                    "scene_id": scene_id,
                    "exact_scene_script_binding_proven": False,
                },
                provider_settings_proof={
                    "effective_route": route_by_scene.get(scene_id),
                    "prior_route": ("PEXELS_VIDEO" if scene_attempts else None),
                    "settings_exact": False,
                },
                checksum_proof={
                    "actual_bytes_rehashed_for_revision": False,
                    "checksum_exact": False,
                },
                qc_proof={
                    "output_specific_technical_qc": False,
                    "output_specific_creative_qc": False,
                    "exact_revision_qc": False,
                },
                reason_codes=reasons,
            )

        for output_key, prior_output in (
            ("rendered_media", state.get("review_media_candidate") or {}),
            ("drive_archive", state.get("drive_archive") or {}),
        ):
            add_entry(
                output_key=output_key,
                classification=(
                    "REQUIRES_NEW_EXECUTION" if prior_output else "MISSING"
                ),
                prior_output=prior_output,
                request_proof={
                    "fresh_target_request_hash": None,
                    "exact_request_match": False,
                },
                script_proof={
                    "effective_script": deepcopy(effective["script"]),
                    "exact_revision_binding_proven": False,
                },
                provider_settings_proof={"settings_exact": False},
                checksum_proof={
                    "actual_bytes_rehashed_for_revision": False,
                    "checksum_exact": False,
                },
                qc_proof={"exact_revision_qc": False},
                reason_codes=(
                    ["PRIOR_OUTPUT_LACKS_EXACT_REVISION_COMPLETE_PROOF"]
                    if prior_output
                    else [f"PRIOR_{output_key.upper()}_MISSING"]
                ),
            )

        reuse_valid = [
            item["output_key"]
            for item in entries
            if item["classification"] == "REUSE_VALID"
        ]
        if any(
            key not in {"narration_audio", "forced_alignment"} for key in reuse_valid
        ):
            raise ValidationFailureError("MR1_REUSE_SCOPE_EXCEEDED")
        if "forced_alignment" in reuse_valid and "narration_audio" not in reuse_valid:
            raise ValidationFailureError("MR1_ALIGNMENT_REUSE_REQUIRES_NARRATION_REUSE")
        counts = {
            state: sum(1 for item in entries if item["classification"] == state)
            for state in (
                "REUSE_VALID",
                "INVALIDATED_BY_REVISION",
                "MISSING",
                "REQUIRES_NEW_EXECUTION",
            )
        }
        return {
            "schema_version": "mr1.reuse-decision-manifest.v1",
            "decision_policy": "FAIL_CLOSED_COMPLETE_EXACT_PROOF_REQUIRED",
            "source_run": {
                "artifact_id": str(source_run.artifact_id),
                "artifact_version_id": str(source_run.id),
                "artifact_version_ref": f"artifact-version://{source_run.id}",
                "version_number": source_run.version_number,
                "content_hash": source_run.content_hash,
                "run_id": state.get("run_id"),
                "current_state": state.get("current_state"),
                "workspace": str(source_workspace)
                if source_workspace is not None
                else None,
            },
            "target_package": PKG1MarketRevisionService._version_ref(package),
            "target_revision": {
                "revision_id": manifest.get("revision_id"),
                "revision_hash": manifest.get("revision_hash"),
                "project_id": str(project.id),
            },
            "effective_artifacts": deepcopy(effective),
            "historical_sc04_attempt_evidence": deepcopy(
                manifest.get("attempt_evidence") or {}
            ),
            "provider_attempt_scope": deepcopy(derived["provider_attempt_scope"]),
            "entries": entries,
            "decision_counts": counts,
            "reuse_allowed_output_keys": reuse_valid,
            "fresh_execution_required_output_keys": [
                item["output_key"]
                for item in entries
                if item["classification"] != "REUSE_VALID"
            ],
            "prior_output_reuse_count": len(reuse_valid),
            "fresh_provider_call_plan": {
                "elevenlabs_narration": (0 if "narration_audio" in reuse_valid else 1),
                "elevenlabs_forced_alignment": (
                    0 if "forced_alignment" in reuse_valid else 1
                ),
            },
            "fresh_elevenlabs_execution_cost_usd": (
                0.0
                if set(reuse_valid) == {"narration_audio", "forced_alignment"}
                else None
            ),
            "old_provider_cost_reused_or_resettled": False,
            "old_approval_authority_reused": False,
            "old_run_authority_reused": False,
            "old_attempt_ledger_authority_reused": False,
            "immutable_output_reuse_requires_fresh_approval": True,
            "canonical_timeline_reuse_authorized": False,
            "supporting_visual_subwindows_reuse_authorized": False,
            "fresh_temporal_compilation_required": True,
            "fresh_caption_compilation_required": True,
            "fresh_run_required": True,
            "fail_closed": True,
            "provider_calls": 0,
            "render_calls": 0,
            "drive_calls": 0,
            "youtube_calls": 0,
        }

    def _supersession_content(
        self,
        *,
        command: MR1ReapprovalCommand,
        approval: ApprovalDecision,
        approval_ref: str,
        evidence: dict[str, Any],
    ) -> dict[str, Any]:
        if evidence["project"].project_type == SC04_PROJECT_TYPE:
            old_items = deepcopy(
                (evidence["pkg1_receipt"].content or {}).get("superseded_mr1_approvals")
                or []
            )
            superseded: list[dict[str, Any]] = []
            for item in old_items:
                try:
                    old_decision = self.session.get(
                        ApprovalDecision,
                        uuid.UUID(str(item["approval_decision_id"])),
                    )
                except (KeyError, TypeError, ValueError) as exc:
                    raise ValidationFailureError(
                        "HISTORICAL_MR1_APPROVAL_REF_INVALID"
                    ) from exc
                if (
                    old_decision is None
                    or old_decision.decision != item.get("decision")
                    or (old_decision.metadata_ or {}).get("approval_scope")
                    != item.get("approval_scope")
                    or item.get("reuse_allowed") is not False
                    or item.get("historical_receipt_mutated") is not False
                ):
                    raise ValidationFailureError(
                        "HISTORICAL_MR1_APPROVAL_LINEAGE_INVALID"
                    )
                superseded.append(
                    {
                        **item,
                        "approval_decision_id": str(old_decision.id),
                        "decision_preserved": old_decision.decision,
                        "state": "SUPERSEDED",
                        "historical_row_state": "PRESERVED",
                        "reuse_allowed": False,
                        "evidence_deleted": False,
                    }
                )
            if not superseded:
                raise ValidationFailureError("HISTORICAL_MR1_APPROVAL_MISSING")
            return {
                "schema_version": "mr1.approval-supersession-ledger.v1",
                "ledger_policy": "APPEND_ONLY_NO_HISTORICAL_MUTATION",
                "replacement_approval_decision_id": str(approval.id),
                "replacement_approval_ref": approval_ref,
                "replacement_revision_id": evidence["package"].content["revision_id"],
                "replacement_revision_hash": evidence["package"].content[
                    "revision_hash"
                ],
                "superseded_approvals": superseded,
                "historical_artifacts_mutated": False,
                "provider_calls": 0,
                "render_calls": 0,
                "drive_calls": 0,
                "youtube_calls": 0,
            }

        old = evidence["package"].content.get("old_mr1_approval") or {}
        old_id = uuid.UUID(old["approval_decision_id"])
        old_decision = self.session.get(ApprovalDecision, old_id)
        if old_decision is None:
            raise ValidationFailureError("HISTORICAL_MR1_APPROVAL_MISSING")
        if (
            str(old_decision.target_artifact_version_id)
            != old.get("target_artifact_version_id")
            or old_decision.decision != "approved"
            or (old_decision.metadata_ or {}).get("approval_scope")
            != "MR1_PAID_EXECUTION"
        ):
            raise ValidationFailureError("HISTORICAL_MR1_APPROVAL_LINEAGE_INVALID")
        return {
            "schema_version": "mr1.approval-supersession-ledger.v1",
            "ledger_policy": "APPEND_ONLY_NO_HISTORICAL_MUTATION",
            "replacement_approval_decision_id": str(approval.id),
            "replacement_approval_ref": approval_ref,
            "replacement_revision_id": evidence["package"].content["revision_id"],
            "replacement_revision_hash": evidence["package"].content["revision_hash"],
            "superseded_approvals": [
                {
                    **deepcopy(old),
                    "approval_decision_id": str(old_decision.id),
                    "decision_preserved": old_decision.decision,
                    "state": "SUPERSEDED",
                    "historical_row_state": "PRESERVED",
                    "reuse_allowed": False,
                    "evidence_deleted": False,
                }
            ],
            "historical_artifacts_mutated": False,
            "provider_calls": 0,
            "render_calls": 0,
            "drive_calls": 0,
            "youtube_calls": 0,
        }

    @staticmethod
    def _approval_decision_authority_payload(
        *,
        project_type: str,
        project_id: uuid.UUID,
        approval_version: int,
        mr1_approval_ref: str,
        decision: str,
        decision_source: str,
        approval_purpose: str,
        execution_mode: str,
        run_type: str,
        channel_key: str,
        operator_decision_text: str,
        exact_target: dict[str, Any],
        exact_bindings: dict[str, Any],
        provider_attempt_scope: dict[str, Any],
        cost_scope: dict[str, Any],
        destination: dict[str, Any],
        human_and_final_media_policy: dict[str, Any],
        reuse_allowed_output_keys: list[str],
        reuse_decision_ref: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Build the one canonical JSON authority persisted in both row and receipt."""

        sc04_revision = project_type == SC04_PROJECT_TYPE
        authority_kind = (
            "pkg1-sc04-revision" if sc04_revision else "pkg1-market-revision"
        )
        operator_authority_ref = (
            f"operator-approval://{authority_kind}/{project_id}/"
            f"mr1-real-production-v{approval_version}"
        )
        reuse_allowed = list(reuse_allowed_output_keys)
        payload = {
            "schema_version": "mr1.approval-decision-authority.v1",
            "metadata": {
                "approval_ref": operator_authority_ref,
                "mr1_approval_ref": mr1_approval_ref,
                "approval_scope": APPROVAL_SCOPE,
                "approval_version": approval_version,
                "decision_source": decision_source,
                "approval_purpose": approval_purpose,
                "execution_mode": execution_mode,
                "run_type": run_type,
                "channel_key": channel_key,
                "operator_decision_text": operator_decision_text,
                "single_run": True,
                "terminal_after_execution_begins": True,
                "production_eligible": True,
                "publishable": False,
                "publish_execution_authorized": False,
                "immutable_output_reuse_authorized": bool(reuse_allowed),
                "reuse_allowed_output_keys": reuse_allowed,
                "old_approval_run_attempt_authority_reused": False,
                "canonical_timeline_reuse_authorized": False,
                "fresh_temporal_compilation_required": sc04_revision,
                "pkg1_approval_decision_id": exact_target["pkg1_approval_decision_id"],
                "pkg1_human_review_receipt_version_id": exact_target[
                    "pkg1_human_review_receipt_version_id"
                ],
                "revision_id": exact_target["revision_id"],
                "revision_hash": exact_target["revision_hash"],
                "package_content_hash": exact_target["package_content_hash"],
            },
            "decision_basis": {
                **{key: "PASS" for key in PASS_VERDICTS},
                **({SC04_REUSE_PASS_VERDICT: "PASS"} if sc04_revision else {}),
                "decision": decision,
                "execution_mode": execution_mode,
                "single_run": True,
                "publish_execution_authorized": False,
                "immutable_output_reuse_authorized": bool(reuse_allowed),
                "reuse_allowed_output_keys": reuse_allowed,
                "old_approval_run_attempt_authority_reused": False,
                "canonical_timeline_reuse_authorized": False,
                "fresh_temporal_compilation_required": sc04_revision,
            },
            "evidence_basis": {
                "exact_target": deepcopy(exact_target),
                "exact_bindings": deepcopy(exact_bindings),
                "provider_attempt_scope": deepcopy(provider_attempt_scope),
                "cost_scope": deepcopy(cost_scope),
                **(
                    {"reuse_decision_manifest": deepcopy(reuse_decision_ref)}
                    if reuse_decision_ref is not None
                    else {}
                ),
            },
            "policy_basis": {
                "destination": deepcopy(destination),
                "human_and_final_media_policy": deepcopy(human_and_final_media_policy),
                "approved_operations": deepcopy(APPROVED_OPERATIONS),
                "prohibited_operations": deepcopy(PROHIBITED_OPERATIONS),
            },
        }
        payload["content_hash"] = content_hash(payload)
        return payload

    def _revalidate_approval_decision_authority(
        self,
        *,
        project: VideoProject,
        approval: ApprovalDecision,
        receipt_content: dict[str, Any],
        reuse_decision: ArtifactVersion | None,
    ) -> None:
        reuse_allowed = (
            list((reuse_decision.content or {}).get("reuse_allowed_output_keys") or [])
            if reuse_decision is not None
            else []
        )
        reuse_ref = (
            PKG1MarketRevisionService._version_ref(reuse_decision)
            if reuse_decision is not None
            else None
        )
        try:
            expected = self._approval_decision_authority_payload(
                project_type=project.project_type,
                project_id=project.id,
                approval_version=receipt_content["approval_version"],
                mr1_approval_ref=receipt_content["approval_ref"],
                decision=receipt_content["decision"],
                decision_source=receipt_content["decision_source"],
                approval_purpose=receipt_content["approval_purpose"],
                execution_mode=receipt_content["execution_mode"],
                run_type=receipt_content["run_type"],
                channel_key=receipt_content["channel_key"],
                operator_decision_text=receipt_content["operator_decision_text"],
                exact_target=receipt_content["exact_target"],
                exact_bindings=receipt_content["exact_bindings"],
                provider_attempt_scope=receipt_content["provider_attempt_scope"],
                cost_scope=receipt_content["cost_scope"],
                destination=receipt_content["destination"],
                human_and_final_media_policy=receipt_content[
                    "human_and_final_media_policy"
                ],
                reuse_allowed_output_keys=reuse_allowed,
                reuse_decision_ref=reuse_ref,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValidationFailureError(
                "MR1_APPROVAL_DECISION_AUTHORITY_SOURCE_INVALID"
            ) from exc

        frozen = deepcopy(receipt_content.get("approval_decision_authority"))
        if not isinstance(frozen, dict):
            raise ValidationFailureError(
                "MR1_APPROVAL_DECISION_AUTHORITY_RECEIPT_MISSING"
            )
        frozen_core = deepcopy(frozen)
        supplied_hash = frozen_core.pop("content_hash", None)
        if not isinstance(supplied_hash, str) or supplied_hash != content_hash(
            frozen_core
        ):
            raise ValidationFailureError(
                "MR1_APPROVAL_DECISION_AUTHORITY_HASH_MISMATCH"
            )
        if frozen != expected:
            raise ValidationFailureError(
                "MR1_APPROVAL_DECISION_AUTHORITY_RECEIPT_MISMATCH"
            )

        row_authority = {
            "metadata": deepcopy(approval.metadata_ or {}),
            "decision_basis": deepcopy(approval.decision_basis or {}),
            "evidence_basis": deepcopy(approval.evidence_basis or {}),
            "policy_basis": deepcopy(approval.policy_basis or {}),
        }
        expected_row_authority = {
            key: deepcopy(expected[key])
            for key in (
                "metadata",
                "decision_basis",
                "evidence_basis",
                "policy_basis",
            )
        }
        if row_authority != expected_row_authority:
            raise ValidationFailureError("MR1_APPROVAL_DECISION_AUTHORITY_ROW_MISMATCH")

    def _readiness_content(
        self,
        *,
        command: MR1ReapprovalCommand,
        approval: ApprovalDecision,
        approval_ref: str,
        derived: dict[str, Any],
        supersession: ArtifactVersion,
        reuse_decision: ArtifactVersion | None,
    ) -> dict[str, Any]:
        reuse_content = (
            deepcopy(reuse_decision.content or {}) if reuse_decision is not None else {}
        )
        reuse_allowed = list(reuse_content.get("reuse_allowed_output_keys") or [])
        reuse_classifications = {
            str(item.get("output_key")): str(item.get("classification"))
            for item in reuse_content.get("entries") or []
            if isinstance(item, dict) and item.get("output_key")
        }
        return {
            "schema_version": "mr1.execution-readiness-preflight.v1",
            "preflight_mode": "READ_ONLY_NO_BILLABLE_CALLS",
            "approval_decision_id": str(approval.id),
            "approval_ref": approval_ref,
            "approval_version": command.approval_version,
            "exact_target": deepcopy(derived["exact_target"]),
            "exact_bindings": deepcopy(derived["exact_bindings"]),
            "lpro1_execution_contract": deepcopy(derived["lpro1_execution_contract"]),
            "upstream_entry": deepcopy(derived["upstream_entry"]),
            "configuration_readiness": deepcopy(derived["configuration_readiness"]),
            "supersession_ledger": PKG1MarketRevisionService._version_ref(supersession),
            **(
                {
                    "reuse_decision_manifest": (
                        PKG1MarketRevisionService._version_ref(reuse_decision)
                    ),
                    "reuse_policy": "ONLY_REUSE_VALID_MAY_BE_CONSUMED",
                    "prior_output_reuse_count": len(reuse_allowed),
                    "reuse_allowed_output_keys": reuse_allowed,
                    "reuse_classifications_hash": content_hash(reuse_classifications),
                    "reuse_manifest_content_hash": reuse_decision.content_hash,
                    "fresh_provider_call_plan": deepcopy(
                        reuse_content.get("fresh_provider_call_plan") or {}
                    ),
                    "fresh_temporal_compilation_required": reuse_content.get(
                        "fresh_temporal_compilation_required"
                    ),
                }
                if reuse_decision is not None
                else {}
            ),
            "checks": {
                "exact_target_resolution": "PASS",
                "hash_validation": "PASS",
                "profile_snapshot_state": "PASS",
                "market_alignment": "PASS",
                "destination_state": "PASS_PENDING_PLATFORM_ID",
                "provider_plan": "PASS",
                "cost_plan": "PASS",
                "rights_disclosure": "PASS_PLANNING_FINAL_AFTER_ACQUISITION",
                "lpro1_contract": "PASS",
                "single_run_terminal_scope": "PASS",
                **(
                    {"reuse_decision_manifest": "PASS_FAIL_CLOSED"}
                    if reuse_decision is not None
                    else {}
                ),
            },
            "MR1_RENDER_DESTINATION_GATE": "PASS",
            "PUBLISH_DESTINATION_GATE": "BLOCKED_PENDING_PLATFORM_ID",
            "MR1_EXECUTION": "NOT_STARTED",
            "MR1_PROVIDER_CALL_COUNT": 0,
            "MR1_RENDER_STATUS": "NOT_STARTED",
            "MR1_HUMAN_REVIEW": "PENDING",
            "PUBLISH_EXECUTION_READY": False,
            "PROCEED_TO_MR1": True,
            "provider_calls": 0,
            "render_calls": 0,
            "drive_calls": 0,
            "youtube_calls": 0,
            "verdicts": {
                **{key: "PASS" for key in PASS_VERDICTS},
                **(
                    {SC04_REUSE_PASS_VERDICT: "PASS"}
                    if reuse_decision is not None
                    else {}
                ),
            },
        }

    def _receipt_content(
        self,
        *,
        command: MR1ReapprovalCommand,
        approval: ApprovalDecision,
        approval_ref: str,
        derived: dict[str, Any],
        supersession: ArtifactVersion,
        readiness: ArtifactVersion,
        reuse_decision: ArtifactVersion | None,
        approval_decision_authority: dict[str, Any],
    ) -> dict[str, Any]:
        content = {
            "schema_version": "mr1.execution-approval-receipt.v1",
            "receipt_content_authority": "ARTIFACT_VERSION_CONTENT_HASH",
            "approval_decision_id": str(approval.id),
            "approval_id": str(approval.id),
            "approval_version": command.approval_version,
            "approval_ref": approval_ref,
            "decision": command.decision,
            "decision_source": command.decision_source,
            "approval_purpose": command.approval_purpose,
            "operator_decision_text": command.operator_decision_text,
            "execution_mode": command.execution_mode,
            "run_type": command.run_type,
            "channel_key": command.channel_key,
            "production_eligible": True,
            "publishable": False,
            "created_at": approval.decided_at.isoformat(),
            "expires_at": None,
            "execution_scope_terminal_policy": (
                "SINGLE_RUN_TERMINAL_AFTER_EXECUTION_BEGINS"
            ),
            "single_run": True,
            "terminal_after_execution_begins": True,
            "exact_target": deepcopy(derived["exact_target"]),
            "exact_bindings": deepcopy(derived["exact_bindings"]),
            "provider_attempt_scope": deepcopy(derived["provider_attempt_scope"]),
            "cost_scope": deepcopy(derived["cost_scope"]),
            "destination": deepcopy(derived["destination"]),
            "rights_disclosure_bindings": {
                key: deepcopy(derived["exact_bindings"][key])
                for key in (
                    "rights_disclosure_completeness_report",
                    "synthetic_media_disclosure_receipt_draft",
                    "asset_provenance_plan",
                    "publish_risk_dossier",
                )
            },
            "lpro1_execution_contract": deepcopy(derived["lpro1_execution_contract"]),
            "human_and_final_media_policy": deepcopy(
                derived["human_and_final_media_policy"]
            ),
            "approval_decision_authority": deepcopy(approval_decision_authority),
            "approved_operations": APPROVED_OPERATIONS,
            "prohibited_operations": PROHIBITED_OPERATIONS,
            "readiness": PKG1MarketRevisionService._version_ref(readiness),
            "supersession_ledger": PKG1MarketRevisionService._version_ref(supersession),
            **(
                {
                    "reuse_decision_manifest": (
                        PKG1MarketRevisionService._version_ref(reuse_decision)
                    ),
                    "reuse_policy": "ONLY_REUSE_VALID_MAY_BE_CONSUMED",
                }
                if reuse_decision is not None
                else {}
            ),
            "publish_prohibited": True,
            "destination_pending_state": "PENDING_PLATFORM_ID",
            "provider_calls": 0,
            "render_calls": 0,
            "drive_calls": 0,
            "youtube_calls": 0,
        }
        content["approval_content_hash"] = content_hash(content)
        return content

    def _create_artifact(
        self,
        *,
        project: VideoProject,
        artifact_type: str,
        content: dict[str, Any],
        actor_id: uuid.UUID,
        evidence_refs: list[dict[str, Any]],
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
                f"UNEXPECTED_EXISTING_MR1_REAPPROVAL_ARTIFACT:{artifact_type}"
            )
        service = ArtifactService(self.session)
        artifact = service.create_artifact(
            data=ArtifactCreate(
                video_project_id=project.id,
                artifact_type=artifact_type,
                status="approved",
                created_by_user_id=actor_id,
            ),
            correlation_id=f"mr1-reapproval-{artifact_type}",
        )
        version = service.create_artifact_version(
            data=ArtifactVersionCreate(
                artifact_id=artifact.id,
                content=deepcopy(content),
                status="approved",
                created_by_user_id=actor_id,
                evidence_refs=deepcopy(evidence_refs),
                packaging_metadata={
                    "mr1_reapproval": True,
                    "execution_mode": "REAL_APPROVED_PRODUCTION",
                    "provider_execution": "NOT_STARTED",
                    "render_execution": "NOT_STARTED",
                    "drive_execution": "NOT_STARTED",
                    "youtube_execution": "PROHIBITED",
                },
            ),
            correlation_id=f"mr1-reapproval-version-{artifact_type}",
        )
        return artifact, version

    def _exact_mr1_artifacts(self, project_id: uuid.UUID) -> dict[str, ArtifactVersion]:
        project = self.session.get(VideoProject, project_id)
        required_types = [
            RECEIPT_ARTIFACT_TYPE,
            READINESS_ARTIFACT_TYPE,
            SUPERSESSION_ARTIFACT_TYPE,
        ]
        if project is not None and project.project_type == SC04_PROJECT_TYPE:
            required_types.append(REUSE_DECISION_ARTIFACT_TYPE)
        rows = list(
            self.session.scalars(
                select(Artifact).where(
                    Artifact.video_project_id == project_id,
                    Artifact.artifact_type.in_(tuple(required_types)),
                )
            ).all()
        )
        grouped: dict[str, list[Artifact]] = {}
        for row in rows:
            grouped.setdefault(row.artifact_type, []).append(row)
        result: dict[str, ArtifactVersion] = {}
        for artifact_type in required_types:
            candidates = grouped.get(artifact_type) or []
            if len(candidates) != 1 or candidates[0].current_version_id is None:
                raise ValidationFailureError(
                    f"EXACT_MR1_ARTIFACT_REQUIRED:{artifact_type}"
                )
            version = self.session.get(
                ArtifactVersion, candidates[0].current_version_id
            )
            if version is None:
                raise ValidationFailureError(
                    f"MR1_ARTIFACT_VERSION_MISSING:{artifact_type}"
                )
            if candidates[0].status != "approved" or version.status != "approved":
                raise ValidationFailureError(
                    f"MR1_ARTIFACT_NOT_APPROVED:{artifact_type}"
                )
            result[artifact_type] = version
        return result

    def _scope_approvals(self, package_version_id: uuid.UUID) -> list[ApprovalDecision]:
        decisions = list(
            self.session.scalars(
                select(ApprovalDecision).where(
                    ApprovalDecision.target_artifact_version_id == package_version_id,
                    ApprovalDecision.decision == "approved",
                )
            ).all()
        )
        return [
            item
            for item in decisions
            if (item.metadata_ or {}).get("approval_scope") == APPROVAL_SCOPE
        ]

    def _version_from_ref(
        self,
        ref: dict[str, Any],
        artifact_type: str,
        *,
        allowed_project_ids: set[uuid.UUID] | None = None,
    ) -> ArtifactVersion:
        try:
            version_id = uuid.UUID(ref["artifact_version_id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValidationFailureError(
                f"MR1_INVALID_ARTIFACT_REF:{artifact_type}"
            ) from exc
        version = self.session.get(ArtifactVersion, version_id)
        artifact = (
            self.session.get(Artifact, version.artifact_id)
            if version is not None
            else None
        )
        if artifact_type == "package_manifest":
            artifact_statuses = {"approved"}
            version_statuses = {"submitted", "approved"}
        else:
            artifact_statuses = {"in_review", "approved"}
            version_statuses = {"submitted", "approved"}
        if (
            version is None
            or artifact is None
            or artifact.artifact_type != artifact_type
            or artifact.status not in artifact_statuses
            or version.status not in version_statuses
            or (
                allowed_project_ids is not None
                and artifact.video_project_id not in allowed_project_ids
            )
            or artifact.current_version_id != version.id
            or str(version.artifact_id) != ref.get("artifact_id")
            or ref.get("artifact_version_ref") != f"artifact-version://{version.id}"
            or version.version_number != ref.get("version_number")
            or version.content_hash != ref.get("content_hash")
            or content_hash(version.content or {}) != version.content_hash
        ):
            raise ValidationFailureError(f"MR1_ARTIFACT_HASH_MISMATCH:{artifact_type}")
        return version

    @staticmethod
    def _validate_existing_command(
        *,
        command: MR1ReapprovalCommand,
        result: dict[str, Any],
    ) -> None:
        target = result["exact_target"]
        bindings = result["exact_bindings"]
        if (
            target["project_id"] != str(command.project_id)
            or target["pkg1_approval_decision_id"]
            != str(command.pkg1_approval_decision_id)
            or target["pkg1_human_review_receipt_version_id"]
            != str(command.pkg1_human_review_receipt_version_id)
            or bindings["channel_profile_version"]["id"]
            != str(command.channel_profile_version_id)
            or bindings["compiled_channel_policy_snapshot"]["id"]
            != str(command.compiled_policy_snapshot_id)
            or result["approval_version"] != command.approval_version
        ):
            raise ValidationFailureError("EXISTING_MR1_REAPPROVAL_TARGET_CONFLICT")
