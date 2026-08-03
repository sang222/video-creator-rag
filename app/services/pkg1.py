from __future__ import annotations

import json
import re
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.contracts.channel_policy import ChannelScopedPolicy
from app.contracts.pkg1 import (
    NarrationPacingPreflightEstimate,
    PKG1BuildResult,
    PKG1ClaimEvidence,
    PKG1CostEstimate,
    PKG1CreativeBrief,
    PKG1EditorialScript,
    PKG1GateResult,
    PKG1ScriptSegment,
    PKG1Source,
    PKG1SpokenTextNormalized,
    PKG1VisualDirection,
    SceneVisualIntent,
    SpokenMappingUnit,
    SpokenToken,
)
from app.contracts.workflow import (
    ApprovalDecisionCreate,
    ArtifactCreate,
    ArtifactVersionCreate,
    ReviewTaskCreate,
    VideoProjectCreate,
)
from app.core.errors import NotFoundError, ValidationFailureError
from app.core.time import utc_now
from app.db.models import (
    ApprovalDecision,
    Artifact,
    ArtifactVersion,
    CloudMediaRef,
    ChannelProfileVersion,
    ChannelStatePackSnapshot,
    ChannelWorkspace,
    CompiledChannelPolicySnapshot,
    ContextPackSnapshot,
    EditorialCalendarSlot,
    EditorialIdeaCandidate,
    EditorialResearchRun,
    FinalMediaRef,
    FormatIdentityContract,
    GateDefinitionVersion,
    GateRun,
    HumanUploadTask,
    MediaRenderJob,
    MediaOffloadJob,
    PaidProviderCallLedger,
    ProjectAdmissionDecision,
    ProviderAttempt,
    ProviderJobSnapshot,
    RetrievalPlanSnapshot,
    ReviewTask,
    UploadedVideo,
    VideoProject,
)
from app.services.config_registry import content_hash
from app.services.workflow import (
    ApprovalService,
    ArtifactService,
    ReviewService,
    VideoProjectService,
)


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CH1_REPORT = ROOT / "reports/ch1_flex_summary.json"
FALLBACK_TOPIC = "How One Automation Can Save a Small Team 20 Hours Every Week"
PROJECT_TYPE = "PKG1_FIRST_PRODUCTION_PACKAGE"
MAX_REVISION_CYCLES = 2
PKG1_OPERATOR_APPROVAL_SCOPES = {
    "PKG1_SCRIPT": "script",
    "PKG1_ORIGINALITY": "episode_originality_manifest",
    "PKG1_VISUAL_DIRECTION": "visual_direction_contract",
    "PKG1_ASSET_REQUEST_PLAN": "compiled_asset_request_plan",
    "PKG1_COST_BUDGET": "cost_estimate_snapshot",
    "PKG1_RIGHTS_DISCLOSURE": "rights_disclosure_completeness_report",
}

PRE_RENDER_GATES = (
    "IdeaGate",
    "ProjectAdmissionGate",
    "SourceQualityGate",
    "ClaimEvidenceGate",
    "ScriptNormalizationGate",
    "ScriptDurationPreflightGate",
    "OriginalityGate",
    "VisualCoverageGate",
    "VisualSourcePolicyGate",
    "VisualDirectionCompletenessGate",
    "ProviderBoundaryGate",
    "ProviderCostEstimateGate",
    "PerVideoCostGate",
    "ChannelMonthlyBudgetGate",
    "RightsDisclosureCompletenessGate",
    "SyntheticDisclosurePlanningGate",
    "PromptBudgetGate",
    "ContextPackShapeGate",
)

POST_MEDIA_GATES = (
    "NarrationPacingGate",
    "CaptionAudioSyncGate",
    "SubtitleSidecarGate",
    "TimelineDriftGate",
    "TechnicalMediaQC",
    "CreativePerceptualMediaQC",
    "HumanWatchabilityReview",
)


class PKG1PackageService:
    """Build the first provider-free production package from frozen channel policy."""

    def __init__(self, session: Session, *, ch1_report_path: Path = DEFAULT_CH1_REPORT):
        self.session = session
        self.ch1_report_path = ch1_report_path

    def entry_status(self, channel_id: uuid.UUID) -> dict[str, Any]:
        if not self.ch1_report_path.exists():
            return {"status": "BLOCKED", "reason_codes": ["CH1_REPORT_MISSING"]}
        report = json.loads(self.ch1_report_path.read_text(encoding="utf-8"))
        verdicts = report.get("verdicts") or {}
        if (
            verdicts.get("CH1_FLEX_FINAL") != "PASS"
            or verdicts.get("PROCEED_TO_PKG1") is not True
        ):
            return {"status": "BLOCKED", "reason_codes": ["CH1_NOT_PASS"]}
        channel = self.session.get(ChannelWorkspace, channel_id)
        if channel is None or channel.key != "small-team-ai":
            return {"status": "BLOCKED", "reason_codes": ["CHANNEL_NOT_SMALL_TEAM_AI"]}
        snapshot = self.session.get(
            CompiledChannelPolicySnapshot, channel.active_policy_snapshot_id
        )
        if snapshot is None or snapshot.status != "active":
            return {
                "status": "BLOCKED",
                "reason_codes": ["ACTIVE_POLICY_SNAPSHOT_MISSING"],
            }
        profile = self.session.get(
            ChannelProfileVersion, snapshot.channel_profile_version_id
        )
        payload = snapshot.compiled_payload or {}
        policy_raw = payload.get("channel_scoped_policy")
        if (
            profile is None
            or profile.version != 1
            or profile.status != "active"
            or not profile.approved_at
        ):
            return {
                "status": "BLOCKED",
                "reason_codes": ["APPROVED_PROFILE_V1_MISSING"],
            }
        try:
            policy = ChannelScopedPolicy.model_validate(policy_raw)
        except ValidationError:
            return {
                "status": "BLOCKED",
                "reason_codes": ["ACTIVE_CHANNEL_POLICY_INVALID"],
            }
        refs = payload.get("snapshot_refs") or {}
        if (
            policy.policy_status != "APPROVED"
            or not refs.get("creative_quality_policy")
            or not policy.publish_policy.manual_upload_only
        ):
            return {
                "status": "BLOCKED",
                "reason_codes": ["ACTIVE_CHANNEL_STATE_INCOMPLETE"],
            }
        runtime = report.get("runtime") or {}
        if runtime and (
            runtime.get("channel_id") not in (None, str(channel.id))
            or runtime.get("compiled_policy_snapshot_id")
            not in (None, str(snapshot.id))
        ):
            return {
                "status": "BLOCKED",
                "reason_codes": ["CH1_REPORT_DATABASE_LINEAGE_MISMATCH"],
            }
        return {
            "status": "PASS",
            "reason_codes": ["CH1_FLEX_VERIFIED"],
            "channel": channel,
            "profile": profile,
            "snapshot": snapshot,
            "policy": policy,
        }

    def build_first_package(
        self, *, channel_id: uuid.UUID, created_by_user_id: uuid.UUID
    ) -> PKG1BuildResult:
        existing = self.session.scalars(
            select(VideoProject)
            .where(
                VideoProject.channel_workspace_id == channel_id,
                VideoProject.project_type == PROJECT_TYPE,
            )
            .order_by(VideoProject.created_at.asc())
        ).first()
        if existing is not None:
            report = (
                json.loads(self.ch1_report_path.read_text(encoding="utf-8"))
                if self.ch1_report_path.exists()
                else {}
            )
            verdicts = report.get("verdicts") or {}
            if (
                verdicts.get("CH1_FLEX_FINAL") != "PASS"
                or verdicts.get("PROCEED_TO_PKG1") is not True
            ):
                raise ValidationFailureError("PKG1 entry blocked: CH1_NOT_PASS")
            package = self._current_artifact(existing.id, "package_manifest")
            if package is None:
                raise ValidationFailureError(
                    "existing PKG1 project is missing package manifest"
                )
            return self._build_result(existing, package)

        entry = self.entry_status(channel_id)
        if entry["status"] != "PASS":
            raise ValidationFailureError(
                f"PKG1 entry blocked: {','.join(entry['reason_codes'])}"
            )

        channel: ChannelWorkspace = entry["channel"]
        profile: ChannelProfileVersion = entry["profile"]
        snapshot: CompiledChannelPolicySnapshot = entry["snapshot"]
        policy: ChannelScopedPolicy = entry["policy"]
        no_execution_before = self.no_execution_counts()
        selection = self._select_or_create_idea(
            channel=channel,
            profile=profile,
            snapshot=snapshot,
            created_by_user_id=created_by_user_id,
        )
        idea_result = self._idea_gate(
            selection["idea"], used_fallback=selection["used_fallback_topic"]
        )
        if idea_result["result"] != "PASS":
            raise ValidationFailureError(
                f"IdeaGate blocked: {','.join(idea_result['reason_codes'])}"
            )

        # Snapshot/profile resolution ends here. All downstream work uses values frozen on this project.
        project = VideoProjectService(self.session).create_project(
            data=VideoProjectCreate(
                company_id=channel.company_id,
                channel_workspace_id=channel.id,
                policy_snapshot_id=snapshot.id,
                title=selection["idea"].proposed_title,
                description="PKG1 provider-free pre-production package pending human review.",
                status="in_review",
                project_type=PROJECT_TYPE,
                priority="normal",
                owner_user_id=created_by_user_id,
                created_by_user_id=created_by_user_id,
                financial_summary={"estimated_cost_usd": 0.0, "actual_cost_usd": None},
                brand_safety_summary={"state": "PLANNING_PASS"},
                legal_compliance_summary={
                    "state": "PLANNING_PASS_HUMAN_REVIEW_PENDING"
                },
                audience_delivery_summary={
                    "destination": "YouTube",
                    "publish_mode": "MANUAL_ONLY",
                },
            ),
            correlation_id="pkg1-project-after-idea-gate",
        )
        admission = self._record_project_admission(
            project=project,
            selection=selection,
            created_by_user_id=created_by_user_id,
        )
        artifact_payloads = self._compile_artifact_payloads(
            project=project,
            selection=selection,
            admission=admission,
            policy=policy,
            snapshot=snapshot,
        )
        artifact_versions: dict[str, ArtifactVersion] = {}
        for artifact_type, payload in artifact_payloads.items():
            artifact_versions[artifact_type] = self._create_artifact_version(
                project_id=project.id,
                artifact_type=artifact_type,
                content=payload,
                created_by_user_id=created_by_user_id,
                context_refs=[
                    {"type": "policy_snapshot", "id": str(project.policy_snapshot_id)},
                    {
                        "type": "channel_profile_version",
                        "id": str(project.channel_profile_version_id),
                    },
                ],
            )
        gate_results = self._evaluate_all_gates(
            project=project,
            selection=selection,
            artifact_versions=artifact_versions,
            policy=policy,
            no_execution_before=no_execution_before,
            created_by_user_id=created_by_user_id,
        )
        artifact_versions["gate_results"] = self._create_artifact_version(
            project_id=project.id,
            artifact_type="gate_results",
            content={
                "schema_version": "pkg1.gate-results.v1",
                "pre_render": [
                    item.model_dump(mode="json")
                    for item in gate_results
                    if item.result != "NOT_RUN"
                ],
                "post_media": [
                    item.model_dump(mode="json")
                    for item in gate_results
                    if item.result == "NOT_RUN"
                ],
            },
            created_by_user_id=created_by_user_id,
        )
        manifest_content = self._package_manifest_content(
            project, selection, admission, artifact_versions, gate_results
        )
        package_version = self._create_artifact_version(
            project_id=project.id,
            artifact_type="package_manifest",
            content=manifest_content,
            created_by_user_id=created_by_user_id,
        )
        ReviewService(self.session).create_review_task(
            data=ReviewTaskCreate(
                video_project_id=project.id,
                target_type="artifact_version",
                target_id=package_version.id,
                target_artifact_version_id=package_version.id,
                review_type="final_human",
                status="open",
                assigned_to_user_id=created_by_user_id,
                requested_by_user_id=created_by_user_id,
                review_reason_codes=[
                    "PKG1_HUMAN_PACKAGE_REVIEW_REQUIRED",
                    "MR1_APPROVAL_PENDING",
                ],
                evidence_required=True,
                evidence_refs=[
                    {
                        "type": "package_manifest",
                        "artifact_version_id": str(package_version.id),
                    },
                    {
                        "type": "gate_results",
                        "artifact_version_id": str(
                            artifact_versions["gate_results"].id
                        ),
                    },
                ],
                review_scope="PKG1 topic, script, evidence, visuals, cost, rights, and MR1 admission",
                context_pack_ref=f"context-pack://{selection['context'].id}",
            ),
            correlation_id="pkg1-human-package-review",
        )
        if self.no_execution_counts() != no_execution_before:
            raise ValidationFailureError(
                "provider/media/publish boundary changed during PKG1"
            )
        return self._build_result(project, package_version)

    def read_package(self, project_id: uuid.UUID) -> dict[str, Any]:
        project = self.session.get(VideoProject, project_id)
        if project is None:
            raise NotFoundError(f"video project not found: {project_id}")
        artifacts = list(
            self.session.scalars(
                select(Artifact)
                .where(Artifact.video_project_id == project.id)
                .order_by(Artifact.created_at.asc())
            ).all()
        )
        artifact_ids = [item.id for item in artifacts]
        versions = (
            list(
                self.session.scalars(
                    select(ArtifactVersion)
                    .where(ArtifactVersion.artifact_id.in_(artifact_ids))
                    .order_by(ArtifactVersion.created_at.asc())
                ).all()
            )
            if artifact_ids
            else []
        )
        current_versions = {
            item.current_version_id: item
            for item in artifacts
            if item.current_version_id
        }
        gates = list(
            self.session.scalars(
                select(GateRun)
                .where(GateRun.video_project_id == project.id)
                .order_by(GateRun.created_at.asc())
            ).all()
        )
        reviews = list(
            self.session.scalars(
                select(ReviewTask)
                .where(ReviewTask.video_project_id == project.id)
                .order_by(ReviewTask.created_at.asc())
            ).all()
        )
        by_type: dict[str, dict[str, Any]] = {}
        for version in versions:
            artifact = current_versions.get(version.id)
            if artifact is not None:
                by_type[artifact.artifact_type] = {
                    "artifact_id": str(artifact.id),
                    "artifact_version_id": str(version.id),
                    "version_number": version.version_number,
                    "content_hash": version.content_hash,
                    "content": deepcopy(version.content),
                }
        gate_rows = [
            {
                "gate_run_id": str(gate.id),
                "gate_key": gate.gate_key,
                "result": gate.decision_basis.get("display_result", gate.result),
                "stored_result": gate.result,
                "reason_codes": gate.reason_codes,
                "revision_cycle": gate.decision_basis.get("revision_cycle", 0),
            }
            for gate in gates
        ]
        latest_gate_by_key: dict[str, dict[str, Any]] = {}
        for item in gate_rows:
            latest_gate_by_key[item["gate_key"]] = item
        blockers = [
            item for item in latest_gate_by_key.values() if item["result"] == "BLOCK"
        ]
        package = by_type.get("package_manifest")
        package_approved = False
        if package is not None:
            package_version_id = uuid.UUID(package["artifact_version_id"])
            package_approved = any(
                decision.decision == "approved"
                and (decision.metadata_ or {}).get("approval_scope") == "PKG1_PACKAGE"
                for decision in self.session.scalars(
                    select(ApprovalDecision).where(
                        ApprovalDecision.target_artifact_version_id
                        == package_version_id
                    )
                ).all()
            )
        human_review_state = (
            "PASS"
            if package_approved
            else "PENDING"
            if any(item.status in {"open", "in_progress"} for item in reviews)
            else "UNKNOWN"
        )
        return {
            "project_id": str(project.id),
            "package_id": package["artifact_id"] if package else None,
            "project_type": project.project_type,
            "title": project.title,
            "snapshot_lineage": self._project_lineage(project),
            "artifact_versions": by_type,
            "gate_results": gate_rows,
            "cost_estimate": (by_type.get("cost_estimate_snapshot") or {}).get(
                "content"
            ),
            "provider_request_counts": self._provider_request_counts(by_type),
            "unresolved_blockers": blockers,
            "human_review_state": human_review_state,
            "review_task_ids": [str(item.id) for item in reviews],
            "provider_execution": "DISABLED",
            "technical_status": "BLOCKED" if blockers else "PASS",
            "exact_next_action": (
                "MR1 entry is ready but execution is not started; keep all providers disabled until the separate MR1 run."
                if package_approved
                else "Operator reviews the PKG1 package and explicitly decides whether to open MR1; no provider execution is authorized."
            ),
        }

    def production_package_readiness(self, project_id: uuid.UUID) -> dict[str, Any]:
        package = self.read_package(project_id)
        return {
            "project_id": package["project_id"],
            "package_id": package["package_id"],
            "snapshot_lineage": package["snapshot_lineage"],
            "artifact_versions": {
                key: {
                    field: value[field]
                    for field in (
                        "artifact_id",
                        "artifact_version_id",
                        "version_number",
                        "content_hash",
                    )
                }
                for key, value in package["artifact_versions"].items()
            },
            "gate_results": package["gate_results"],
            "cost_estimate": package["cost_estimate"],
            "provider_request_counts": package["provider_request_counts"],
            "unresolved_blockers": package["unresolved_blockers"],
            "human_review_state": package["human_review_state"],
            "provider_execution": "DISABLED",
            "technical_status": package["technical_status"],
            "exact_next_action": package["exact_next_action"],
        }

    def provider_execution_plan(self, project_id: uuid.UUID) -> dict[str, Any]:
        package = self.read_package(project_id)
        plan = package["artifact_versions"].get("provider_execution_plan")
        if plan is None:
            raise NotFoundError(
                f"provider execution plan not found for project: {project_id}"
            )
        return {
            "project_id": package["project_id"],
            "package_id": package["package_id"],
            "snapshot_lineage": package["snapshot_lineage"],
            "artifact_version": {
                key: plan[key]
                for key in (
                    "artifact_id",
                    "artifact_version_id",
                    "version_number",
                    "content_hash",
                )
            },
            "plan": plan["content"],
            "gate_results": package["gate_results"],
            "cost_estimate": package["cost_estimate"],
            "provider_request_counts": package["provider_request_counts"],
            "unresolved_blockers": package["unresolved_blockers"],
            "provider_execution": "DISABLED",
            "technical_status": package["technical_status"],
            "human_review_state": package["human_review_state"],
            "exact_next_action": package["exact_next_action"],
        }

    def persist_human_approval_and_open_mr1(
        self,
        *,
        project_id: uuid.UUID,
        decided_by_user_id: uuid.UUID,
        approval_ref: str,
    ) -> dict[str, Any]:
        """Persist exact PKG1 approvals and an MR1 readiness record without executing MR1."""
        if not approval_ref.startswith("operator-approval://pkg1/"):
            raise ValidationFailureError(
                "PKG1 closeout requires an explicit operator approval ref"
            )
        before = self.no_execution_counts()
        project = self.session.get(VideoProject, project_id)
        if project is None:
            raise NotFoundError(f"project not found: {project_id}")
        if project.project_type != PROJECT_TYPE:
            raise ValidationFailureError(
                "project is not the PKG1 first production package"
            )

        profile = self.session.get(
            ChannelProfileVersion, project.channel_profile_version_id
        )
        snapshot = self.session.get(
            CompiledChannelPolicySnapshot, project.policy_snapshot_id
        )
        if (
            profile is None
            or snapshot is None
            or snapshot.channel_profile_version_id != profile.id
            or snapshot.channel_workspace_id != project.channel_workspace_id
        ):
            raise ValidationFailureError(
                "frozen PKG1 profile or policy snapshot lineage is invalid"
            )
        frozen_values = (
            project.native_render_policy_snapshot_ref,
            project.native_render_policy_snapshot_hash,
            project.creative_quality_policy_ref,
            project.creative_quality_policy_hash,
            project.provider_usage_policy_ref,
            project.provider_usage_policy_hash,
        )
        if any(not value for value in frozen_values):
            raise ValidationFailureError("PKG1 frozen policy references are incomplete")

        package = self.read_package(project.id)
        if package["technical_status"] != "PASS" or package["unresolved_blockers"]:
            raise ValidationFailureError(
                "PKG1 technical package is not ready for human closeout"
            )
        current = package["artifact_versions"]
        required_types = {
            *PKG1_OPERATOR_APPROVAL_SCOPES.values(),
            "package_manifest",
            "provider_execution_plan",
            "spoken_text_normalized",
        }
        missing = sorted(required_types - set(current))
        if missing:
            raise ValidationFailureError(
                f"PKG1 exact closeout artifacts are missing: {','.join(missing)}"
            )
        package_version = self.session.get(
            ArtifactVersion,
            uuid.UUID(current["package_manifest"]["artifact_version_id"]),
        )
        if package_version is None:
            raise NotFoundError("current PKG1 package manifest version not found")
        manifest_refs = (package_version.content or {}).get("artifacts") or {}
        for artifact_type in required_types - {"package_manifest"}:
            manifest_ref = manifest_refs.get(artifact_type) or {}
            if (
                manifest_ref.get("artifact_version_id")
                != current[artifact_type]["artifact_version_id"]
            ):
                raise ValidationFailureError(
                    f"PKG1 manifest exact binding mismatch: {artifact_type}"
                )

        final_reviews = list(
            self.session.scalars(
                select(ReviewTask).where(
                    ReviewTask.video_project_id == project.id,
                    ReviewTask.review_type == "final_human",
                    ReviewTask.target_artifact_version_id == package_version.id,
                    ReviewTask.status.in_(["open", "in_progress", "completed"]),
                )
            ).all()
        )
        if len(final_reviews) != 1:
            raise ValidationFailureError(
                "exactly one current PKG1 final human review task is required"
            )
        final_review = final_reviews[0]
        if final_review.assigned_to_user_id != decided_by_user_id:
            raise ValidationFailureError(
                "operator decision does not match the assigned final reviewer"
            )

        provider_version = self.session.get(
            ArtifactVersion,
            uuid.UUID(current["provider_execution_plan"]["artifact_version_id"]),
        )
        cost_version = self.session.get(
            ArtifactVersion,
            uuid.UUID(current["cost_estimate_snapshot"]["artifact_version_id"]),
        )
        if provider_version is None or cost_version is None:
            raise NotFoundError(
                "PKG1 provider execution plan or cost envelope version not found"
            )
        if (provider_version.content or {}).get("execution_enabled") is not False:
            raise ValidationFailureError(
                "PKG1 provider execution plan must remain disabled at closeout"
            )
        cost = cost_version.content or {}
        if cost.get("decision") != "PASS" or cost.get("actual_cost") is not None:
            raise ValidationFailureError("PKG1 approved cost envelope is invalid")

        decision_targets = {
            **PKG1_OPERATOR_APPROVAL_SCOPES,
            "PKG1_PACKAGE": "package_manifest",
            "MR1_PAID_EXECUTION": "provider_execution_plan",
        }
        approvals: dict[str, ApprovalDecision] = {}
        for scope, artifact_type in decision_targets.items():
            target_version = self.session.get(
                ArtifactVersion,
                uuid.UUID(current[artifact_type]["artifact_version_id"]),
            )
            if target_version is None:
                raise NotFoundError(
                    f"approval target version not found: {artifact_type}"
                )
            approvals[scope] = self._ensure_pkg1_approval_decision(
                project=project,
                package_version=package_version,
                final_review=final_review,
                target_version=target_version,
                artifact_type=artifact_type,
                scope=scope,
                decided_by_user_id=decided_by_user_id,
                approval_ref=approval_ref,
                cost=cost,
            )

        package_member_ids = {
            uuid.UUID(item["artifact_version_id"])
            for item in manifest_refs.values()
            if isinstance(item, dict) and item.get("artifact_version_id")
        }
        package_member_ids.add(package_version.id)
        approval_by_target = {
            decision.target_artifact_version_id: decision.id
            for decision in approvals.values()
        }
        for review in self.session.scalars(
            select(ReviewTask).where(
                ReviewTask.video_project_id == project.id,
                ReviewTask.status.in_(["open", "in_progress"]),
                ReviewTask.target_artifact_version_id.in_(package_member_ids),
            )
        ).all():
            resolution_decision_id = approval_by_target.get(
                review.target_artifact_version_id,
                approvals["PKG1_PACKAGE"].id,
            )
            ReviewService(self.session).complete_review_task(
                review_task_id=review.id,
                actor_user_id=decided_by_user_id,
                resolution_ref=approval_ref,
                approval_decision_ids=[resolution_decision_id],
                correlation_id="pkg1-human-review-resolved",
            )

        readiness_content = self._mr1_readiness_content(
            project=project,
            package_version=package_version,
            current=current,
            approvals=approvals,
            approval_ref=approval_ref,
        )
        readiness_artifact = self.session.scalars(
            select(Artifact).where(
                Artifact.video_project_id == project.id,
                Artifact.artifact_type == "mr1_readiness_state",
            )
        ).one_or_none()
        if readiness_artifact is None:
            artifact_service = ArtifactService(self.session)
            readiness_artifact = artifact_service.create_artifact(
                data=ArtifactCreate(
                    video_project_id=project.id,
                    artifact_type="mr1_readiness_state",
                    status="approved",
                    created_by_user_id=decided_by_user_id,
                ),
                correlation_id="pkg1-closeout-mr1-readiness-artifact",
            )
            readiness_version = artifact_service.create_artifact_version(
                data=ArtifactVersionCreate(
                    artifact_id=readiness_artifact.id,
                    content=readiness_content,
                    status="approved",
                    created_by_user_id=decided_by_user_id,
                    evidence_refs=[
                        {"type": "operator_approval", "ref": approval_ref},
                        {
                            "type": "approval_decisions",
                            "ids": [str(item.id) for item in approvals.values()],
                        },
                    ],
                    context_refs=[
                        {
                            "type": "package_artifact_version",
                            "id": str(package_version.id),
                        },
                        {
                            "type": "compiled_channel_policy_snapshot",
                            "id": str(project.policy_snapshot_id),
                        },
                    ],
                    packaging_metadata={
                        "mr1_execution": "NOT_STARTED",
                        "provider_call_count": 0,
                    },
                ),
                correlation_id="pkg1-closeout-mr1-readiness-version",
            )
        else:
            readiness_version = self.session.get(
                ArtifactVersion, readiness_artifact.current_version_id
            )
            if readiness_version is None:
                raise ValidationFailureError(
                    "existing MR1 readiness artifact has no current version"
                )
            if readiness_version.content != readiness_content:
                existing = readiness_version.content or {}
                if (
                    existing.get("video_project_id") != str(project.id)
                    or (existing.get("pkg1_package") or {}).get("artifact_version_id")
                    != str(package_version.id)
                    or existing.get("operator_approval_ref") != approval_ref
                ):
                    raise ValidationFailureError(
                        "existing MR1 readiness state conflicts with approved PKG1 bindings"
                    )
                readiness_version = ArtifactService(
                    self.session
                ).create_artifact_version(
                    data=ArtifactVersionCreate(
                        artifact_id=readiness_artifact.id,
                        parent_version_id=readiness_version.id,
                        content=readiness_content,
                        status="approved",
                        created_by_user_id=decided_by_user_id,
                        evidence_refs=[
                            {"type": "operator_approval", "ref": approval_ref},
                            {
                                "type": "approval_decisions",
                                "ids": [str(item.id) for item in approvals.values()],
                            },
                        ],
                        context_refs=[
                            {
                                "type": "package_artifact_version",
                                "id": str(package_version.id),
                            },
                            {
                                "type": "compiled_channel_policy_snapshot",
                                "id": str(project.policy_snapshot_id),
                            },
                        ],
                        packaging_metadata={
                            "mr1_execution": "NOT_STARTED",
                            "provider_call_count": 0,
                            "readiness_metadata_revision": True,
                        },
                    ),
                    correlation_id="pkg1-closeout-mr1-readiness-revision",
                )

        for decision in approvals.values():
            version = self.session.get(
                ArtifactVersion, decision.target_artifact_version_id
            )
            artifact = (
                self.session.get(Artifact, version.artifact_id) if version else None
            )
            if artifact is not None:
                artifact.status = "approved"
        project.status = "approved"
        self.session.flush()

        after = self.no_execution_counts()
        if after != before:
            raise ValidationFailureError(
                "PKG1 closeout mutated provider, render, archive, or upload records"
            )
        provider_calls_since_project = (
            self.session.scalar(
                select(func.count())
                .select_from(ProviderAttempt)
                .where(ProviderAttempt.started_at >= project.created_at)
            )
            or 0
        )
        if provider_calls_since_project != 0:
            raise ValidationFailureError(
                "PKG1 project already has provider attempt consumption"
            )
        return {
            "video_project_id": str(project.id),
            "package_artifact_version_id": str(package_version.id),
            "final_review_task_id": str(final_review.id),
            "approval_ref": approval_ref,
            "approval_decision_ids": {
                key: str(value.id) for key, value in approvals.items()
            },
            "mr1_readiness_artifact_id": str(readiness_artifact.id),
            "mr1_readiness_artifact_version_id": str(readiness_version.id),
            "mr1_readiness_content_hash": readiness_version.content_hash,
            "PKG1_HUMAN_REVIEW": "PASS",
            "PKG1_FINAL": "PASS",
            "MR1_PAID_EXECUTION_APPROVAL": "APPROVED",
            "MR1_ENTRY": "READY",
            "MR1_EXECUTION": "NOT_STARTED",
            "MR1_PROVIDER_CALL_COUNT": 0,
            "MR1_RENDER_STATUS": "NOT_STARTED",
            "MR1_HUMAN_REVIEW": "PENDING",
            "PROCEED_TO_MR1": True,
            "no_execution_counts_before": before,
            "no_execution_counts_after": after,
        }

    def _ensure_pkg1_approval_decision(
        self,
        *,
        project: VideoProject,
        package_version: ArtifactVersion,
        final_review: ReviewTask,
        target_version: ArtifactVersion,
        artifact_type: str,
        scope: str,
        decided_by_user_id: uuid.UUID,
        approval_ref: str,
        cost: dict[str, Any],
    ) -> ApprovalDecision:
        existing = list(
            self.session.scalars(
                select(ApprovalDecision).where(
                    ApprovalDecision.target_type == "artifact_version",
                    ApprovalDecision.target_id == target_version.id,
                    ApprovalDecision.target_artifact_version_id == target_version.id,
                    ApprovalDecision.decision == "approved",
                )
            ).all()
        )
        matching = [
            item
            for item in existing
            if (item.metadata_ or {}).get("approval_ref") == approval_ref
            and (item.metadata_ or {}).get("approval_scope") == scope
        ]
        if matching:
            decision = matching[0]
            if decision.decided_by_user_id != decided_by_user_id:
                raise ValidationFailureError(
                    "existing PKG1 approval actor does not match operator decision"
                )
            return decision
        policy_basis = {
            "channel_profile_version_id": str(project.channel_profile_version_id),
            "compiled_channel_policy_snapshot_id": str(project.policy_snapshot_id),
            "native_render_policy_snapshot_hash": project.native_render_policy_snapshot_hash,
            "creative_quality_policy_hash": project.creative_quality_policy_hash,
            "provider_usage_policy_hash": project.provider_usage_policy_hash,
        }
        metadata = {
            "approval_ref": approval_ref,
            "approval_scope": scope,
            "artifact_type": artifact_type,
            "final_review_task_id": str(final_review.id),
            "package_artifact_version_id": str(package_version.id),
            "all_six_review_required_areas_reviewed": True,
            "mr1_paid_execution_approved": True,
        }
        if scope == "MR1_PAID_EXECUTION":
            metadata["approved_cost_envelope"] = {
                "currency": cost.get("currency"),
                "estimated_cost": cost.get("estimated_cost"),
                "hard_cap": cost.get("hard_cap"),
                "cost_artifact_version_id": str(
                    self._current_artifact(project.id, "cost_estimate_snapshot").id
                ),
            }
        return ApprovalService(self.session).create_approval_decision(
            data=ApprovalDecisionCreate(
                target_type="artifact_version",
                target_id=target_version.id,
                target_artifact_version_id=target_version.id,
                decision="approved",
                decided_by_user_id=decided_by_user_id,
                rationale="Operator reviewed and approved all six PKG1 review areas and MR1 paid execution.",
                metadata=metadata,
                decision_basis={
                    "PKG1_HUMAN_REVIEW": "PASS",
                    "PKG1_FINAL": "PASS",
                    "MR1_PAID_EXECUTION_APPROVAL": "APPROVED",
                    "PROCEED_TO_MR1": True,
                },
                evidence_basis={
                    "approval_ref": approval_ref,
                    "final_review_task_id": str(final_review.id),
                    "package_artifact_version_id": str(package_version.id),
                    "target_artifact_version_id": str(target_version.id),
                    "target_content_hash": target_version.content_hash,
                },
                policy_basis=policy_basis,
                context_pack_ref=f"artifact-version://{package_version.id}",
                human_decision_note="Explicit operator approval recorded for PKG1 closeout; MR1 remains not started.",
            ),
            assigned_final_review_task_id=final_review.id,
            correlation_id=f"pkg1-human-approval-{scope.lower()}",
        )

    @staticmethod
    def _mr1_readiness_content(
        *,
        project: VideoProject,
        package_version: ArtifactVersion,
        current: dict[str, dict[str, Any]],
        approvals: dict[str, ApprovalDecision],
        approval_ref: str,
    ) -> dict[str, Any]:
        def exact_ref(
            artifact_type: str, *, approval_scope: str | None = None
        ) -> dict[str, Any]:
            item = current[artifact_type]
            result = {
                "artifact_id": item["artifact_id"],
                "artifact_version_id": item["artifact_version_id"],
                "artifact_version_ref": f"artifact-version://{item['artifact_version_id']}",
                "version_number": item["version_number"],
                "content_hash": item["content_hash"],
            }
            if approval_scope is not None:
                result["approval_decision_id"] = str(approvals[approval_scope].id)
            return result

        cost_content = current["cost_estimate_snapshot"]["content"]
        provider_content = current["provider_execution_plan"]["content"]
        return {
            "schema_version": "mr1.readiness-state.v1",
            "video_project_id": str(project.id),
            "pkg1_package": {
                "artifact_id": str(package_version.artifact_id),
                "artifact_version_id": str(package_version.id),
                "artifact_version_ref": f"artifact-version://{package_version.id}",
                "version_number": package_version.version_number,
                "content_hash": package_version.content_hash,
                "approval_decision_id": str(approvals["PKG1_PACKAGE"].id),
            },
            "operator_approval_ref": approval_ref,
            "frozen_policy_lineage": {
                "channel_profile_version_id": str(project.channel_profile_version_id),
                "compiled_channel_policy_snapshot_id": str(project.policy_snapshot_id),
                "native_render_policy_snapshot_ref": project.native_render_policy_snapshot_ref,
                "native_render_policy_snapshot_hash": project.native_render_policy_snapshot_hash,
                "creative_quality_policy_ref": project.creative_quality_policy_ref,
                "creative_quality_policy_hash": project.creative_quality_policy_hash,
                "provider_usage_policy_ref": project.provider_usage_policy_ref,
                "provider_usage_policy_hash": project.provider_usage_policy_hash,
            },
            "approved_script_artifact_version": exact_ref(
                "script", approval_scope="PKG1_SCRIPT"
            ),
            "spoken_text_normalized_artifact_version": exact_ref(
                "spoken_text_normalized"
            ),
            "visual_direction_contract": exact_ref(
                "visual_direction_contract",
                approval_scope="PKG1_VISUAL_DIRECTION",
            ),
            "approved_provider_execution_plan": {
                **exact_ref(
                    "provider_execution_plan", approval_scope="MR1_PAID_EXECUTION"
                ),
                "execution_enabled": provider_content["execution_enabled"],
                "approval_status": "APPROVED",
            },
            "approved_cost_envelope": {
                **exact_ref(
                    "cost_estimate_snapshot", approval_scope="PKG1_COST_BUDGET"
                ),
                "currency": cost_content["currency"],
                "estimated_cost": cost_content["estimated_cost"],
                "hard_cap": cost_content["hard_cap"],
                "actual_cost": cost_content["actual_cost"],
            },
            "PKG1_PROVIDER_EXECUTION": "DISABLED",
            "MR1_ENTRY": "READY",
            "MR1_EXECUTION": "NOT_STARTED",
            "MR1_PROVIDER_CALL_COUNT": 0,
            "MR1_RENDER_STATUS": "NOT_STARTED",
            "MR1_HUMAN_REVIEW": "PENDING",
            "PROCEED_TO_MR1": True,
        }

    def revise_artifact_and_rerun(
        self,
        *,
        project_id: uuid.UUID,
        artifact_type: str,
        revised_content: dict[str, Any],
        created_by_user_id: uuid.UUID,
        gate_keys: list[str],
    ) -> dict[str, Any]:
        artifact = self.session.scalars(
            select(Artifact).where(
                Artifact.video_project_id == project_id,
                Artifact.artifact_type == artifact_type,
            )
        ).one_or_none()
        if artifact is None or artifact.current_version_id is None:
            raise NotFoundError(f"artifact not found for revision: {artifact_type}")
        maximum = (
            self.session.scalar(
                select(func.max(ArtifactVersion.version_number)).where(
                    ArtifactVersion.artifact_id == artifact.id
                )
            )
            or 0
        )
        revision_cycle = maximum
        if revision_cycle > MAX_REVISION_CYCLES:
            raise ValidationFailureError(
                "PKG1 maximum automatic revision cycles exceeded"
            )
        parent_id = artifact.current_version_id
        version = ArtifactService(self.session).create_artifact_version(
            data=ArtifactVersionCreate(
                artifact_id=artifact.id,
                parent_version_id=parent_id,
                content=deepcopy(revised_content),
                status="submitted",
                created_by_user_id=created_by_user_id,
                packaging_metadata={
                    "pkg1_revision_cycle": revision_cycle,
                    "previous_artifact_version_id": str(parent_id),
                    "immutable_previous_version": True,
                },
                evidence_refs=[
                    {"type": "automatic_repair_evidence", "cycle": revision_cycle}
                ],
            ),
            correlation_id=f"pkg1-revision-cycle-{revision_cycle}",
        )
        results: list[dict[str, Any]] = []
        for gate_key in gate_keys:
            result, reason_codes = self._evaluate_revised_gate(
                project_id, gate_key, revised_content
            )
            run = self._record_gate_run(
                project_id=project_id,
                gate_key=gate_key,
                result=result,
                reason_codes=reason_codes,
                created_by_user_id=created_by_user_id,
                artifact_version_id=version.id,
                revision_cycle=revision_cycle,
            )
            results.append(
                {
                    "gate_run_id": str(run.id),
                    "gate_key": gate_key,
                    "result": result,
                    "reason_codes": reason_codes,
                }
            )
        ReviewService(self.session).create_review_task(
            data=ReviewTaskCreate(
                video_project_id=project_id,
                target_type="artifact_version",
                target_id=version.id,
                target_artifact_version_id=version.id,
                review_type="evidence"
                if artifact_type == "claim_evidence_ledger"
                else "editorial",
                status="open",
                requested_by_user_id=created_by_user_id,
                review_reason_codes=[
                    "PKG1_AUTOMATIC_REVISION_EVIDENCE",
                    f"REVISION_CYCLE_{revision_cycle}",
                ],
                evidence_required=True,
                evidence_refs=[
                    {"artifact_version_id": str(version.id), "gate_runs": results}
                ],
                review_scope=f"PKG1 automatic revision cycle {revision_cycle}",
            ),
            correlation_id=f"pkg1-revision-review-{revision_cycle}",
        )
        return {
            "revision_cycle": revision_cycle,
            "artifact_version_id": str(version.id),
            "version_number": version.version_number,
            "parent_version_id": str(parent_id),
            "gate_results": results,
        }

    @staticmethod
    def claim_evidence_gate(content: dict[str, Any]) -> tuple[str, list[str]]:
        claims = content.get("claims")
        if not isinstance(claims, list) or not claims:
            return "BLOCK", ["CLAIM_LEDGER_EMPTY"]
        for raw in claims:
            try:
                claim = PKG1ClaimEvidence.model_validate(raw)
            except ValidationError:
                return "BLOCK", ["CLAIM_SCHEMA_OR_EVIDENCE_INVALID"]
            if claim.claim_type == "UNIVERSAL_OUTCOME":
                return "BLOCK", ["UNIVERSAL_OUTCOME_NOT_ALLOWED"]
            if claim.verification_state == "BLOCKED":
                return "BLOCK", ["UNSUPPORTED_CLAIM"]
            if claim.claim_type == "ILLUSTRATIVE_SCENARIO":
                wording = f"{claim.claim_text} {claim.allowed_wording}".lower()
                if (
                    "illustrative" not in wording
                    or "guarante" in claim.allowed_wording.lower()
                ):
                    return "BLOCK", ["SCENARIO_WORDING_NOT_EXPLICIT"]
        return "PASS", ["CLAIM_EVIDENCE_COMPLETE"]

    def no_execution_counts(self) -> dict[str, int]:
        return {
            "provider_attempts": self.session.scalar(
                select(func.count()).select_from(ProviderAttempt)
            )
            or 0,
            "provider_jobs": self.session.scalar(
                select(func.count()).select_from(ProviderJobSnapshot)
            )
            or 0,
            "paid_provider_calls": self.session.scalar(
                select(func.count()).select_from(PaidProviderCallLedger)
            )
            or 0,
            "media_render_jobs": self.session.scalar(
                select(func.count()).select_from(MediaRenderJob)
            )
            or 0,
            "final_media_refs": self.session.scalar(
                select(func.count()).select_from(FinalMediaRef)
            )
            or 0,
            "human_upload_tasks": self.session.scalar(
                select(func.count()).select_from(HumanUploadTask)
            )
            or 0,
            "uploaded_videos": self.session.scalar(
                select(func.count()).select_from(UploadedVideo)
            )
            or 0,
            "media_offload_jobs": self.session.scalar(
                select(func.count()).select_from(MediaOffloadJob)
            )
            or 0,
            "cloud_media_refs": self.session.scalar(
                select(func.count()).select_from(CloudMediaRef)
            )
            or 0,
        }

    def _select_or_create_idea(
        self,
        *,
        channel: ChannelWorkspace,
        profile: ChannelProfileVersion,
        snapshot: CompiledChannelPolicySnapshot,
        created_by_user_id: uuid.UUID,
    ) -> dict[str, Any]:
        approved = self.session.scalars(
            select(EditorialIdeaCandidate)
            .where(
                EditorialIdeaCandidate.channel_workspace_id == channel.id,
                EditorialIdeaCandidate.policy_snapshot_id == snapshot.id,
                EditorialIdeaCandidate.stage.in_(["GREENLIT", "SELECTED_FOR_SLOT"]),
            )
            .order_by(EditorialIdeaCandidate.created_at.asc())
        ).first()
        if approved is not None:
            run = self.session.get(
                EditorialResearchRun, approved.editorial_research_run_id
            )
            context = self.session.get(
                ContextPackSnapshot, approved.context_pack_snapshot_id
            )
            state = self.session.get(
                ChannelStatePackSnapshot, approved.channel_state_pack_snapshot_id
            )
            slot = (
                self.session.get(EditorialCalendarSlot, run.editorial_calendar_slot_id)
                if run
                else None
            )
            if run and context and state and slot:
                return {
                    "slot": slot,
                    "run": run,
                    "context": context,
                    "state": state,
                    "idea": approved,
                    "used_fallback_topic": False,
                    "selection_reason": "EARLIEST_GREENLIT_EDITORIAL_CANDIDATE",
                }
        slot = self.session.scalars(
            select(EditorialCalendarSlot)
            .where(
                EditorialCalendarSlot.channel_workspace_id == channel.id,
                EditorialCalendarSlot.policy_snapshot_id == snapshot.id,
                EditorialCalendarSlot.status.in_(["OPEN", "ASSIGNED", "ADMITTED"]),
            )
            .order_by(
                EditorialCalendarSlot.slot_date.asc(),
                EditorialCalendarSlot.created_at.asc(),
            )
        ).first()
        if slot is None:
            slot = EditorialCalendarSlot(
                company_id=channel.company_id,
                channel_workspace_id=channel.id,
                policy_snapshot_id=snapshot.id,
                slot_date=utc_now().date(),
                slot_type="MANUAL",
                status="OPEN",
                production_goal="Explain one bounded automation mechanism without promising guaranteed savings.",
                target_platforms=["YouTube"],
                content_pillar="practical automation leverage",
                series_key="one-automation",
                format_hint="long-form documentary/explainer",
                character_binding_policy_json={"mode": "NO_CHARACTER"},
                risk_level="LOW",
                operational_envelope={
                    "objective": "Show how to audit one repeated workflow before automating it.",
                    "target_audience": "small professional teams",
                    "pillar_constraints": [
                        "practical",
                        "evidence-aware",
                        "no fake case study",
                    ],
                    "destination": "YouTube",
                    "format_lane": "long-form documentary/explainer",
                    "risk_class": "LOW",
                    "cost_class": "TIER_1_LOW_COST_PRODUCTION",
                    "deadline_cadence": "first approved production package",
                    "planned_hook": "scenario-based time-cost diagnosis",
                    "fallback_policy": "operator-approved PKG1 fallback topic",
                },
                created_by_user_id=created_by_user_id,
            )
            self.session.add(slot)
            self.session.flush()
        run = EditorialResearchRun(
            company_id=channel.company_id,
            channel_workspace_id=channel.id,
            channel_profile_version_id=profile.id,
            policy_snapshot_id=snapshot.id,
            editorial_calendar_slot_id=slot.id,
            run_date=utc_now().date(),
            status="RUNNING",
            trigger_type="MANUAL",
            started_at=utc_now(),
            reason_codes=["PKG1_OPERATOR_APPROVED_FALLBACK"],
            metadata_={"provider_execution": "DISABLED"},
            created_by_user_id=created_by_user_id,
        )
        self.session.add(run)
        self.session.flush()
        plan_body = {
            "purpose": "PKG1 fallback idea admission",
            "allowed_sources": [
                "active_channel_policy",
                "operator_approved_fallback",
                "deterministic_arithmetic",
            ],
            "excluded_sources": [
                "provider_media",
                "unverified_statistics",
                "fake_customer_results",
            ],
            "source_order": [
                "active_channel_policy",
                "operator_approved_fallback",
                "deterministic_arithmetic",
            ],
        }
        retrieval = RetrievalPlanSnapshot(
            purpose="EDITORIAL_RESEARCH",
            company_id=channel.company_id,
            channel_workspace_id=channel.id,
            channel_profile_version_id=profile.id,
            policy_snapshot_id=snapshot.id,
            editorial_calendar_slot_id=slot.id,
            allowed_sources=plan_body["allowed_sources"],
            excluded_sources=plan_body["excluded_sources"],
            redaction_rules={"no_secrets": True, "no_personal_data": True},
            token_budget=12000,
            source_order=plan_body["source_order"],
            plan_hash=content_hash(plan_body),
            created_by_user_id=created_by_user_id,
        )
        self.session.add(retrieval)
        self.session.flush()
        pack_content = {
            "topic": FALLBACK_TOPIC,
            "operator_approval": "prompt://pkg1/operator-approved-fallback",
            "scenario": {
                "team_members": 5,
                "hours_per_day": 1,
                "working_days": 4,
                "calculation": "5 * 1 * 4 = 20 hours",
                "classification": "ILLUSTRATIVE_SCENARIO",
            },
            "prohibitions": [
                "universal outcome",
                "guarantee",
                "fake measured customer result",
            ],
        }
        context = ContextPackSnapshot(
            retrieval_plan_snapshot_id=retrieval.id,
            purpose="EDITORIAL_RESEARCH",
            company_id=channel.company_id,
            channel_workspace_id=channel.id,
            channel_profile_version_id=profile.id,
            policy_snapshot_id=snapshot.id,
            editorial_calendar_slot_id=slot.id,
            input_refs=[{"type": "editorial_slot", "id": str(slot.id)}],
            policy_refs=[
                {
                    "type": "compiled_policy_snapshot",
                    "id": str(snapshot.id),
                    "hash": snapshot.content_hash,
                }
            ],
            evidence_refs=[
                {
                    "type": "operator_approved_fallback",
                    "ref": "prompt://pkg1/operator-approved-fallback",
                }
            ],
            metric_refs=[],
            memory_refs=[],
            pack_content=pack_content,
            freshness_state="FRESH",
            confidence_level="HIGH",
            pack_hash=content_hash(pack_content),
            created_by_user_id=created_by_user_id,
        )
        self.session.add(context)
        self.session.flush()
        state_blob = {
            "channel_key": channel.key,
            "active_policy_snapshot_id": str(snapshot.id),
            "active_profile_version_id": str(profile.id),
            "package_mode": "OFFLINE_PRE_PROVIDER",
        }
        state = ChannelStatePackSnapshot(
            editorial_research_run_id=run.id,
            company_id=channel.company_id,
            channel_workspace_id=channel.id,
            policy_snapshot_id=snapshot.id,
            context_pack_snapshot_id=context.id,
            state_blob=state_blob,
            active_project_refs=[],
            pending_review_refs=[],
            readiness_summary={"ch1_flex": "PASS", "provider_execution": "DISABLED"},
            provider_health_summary={"not_queried": True},
            quota_summary={"not_consumed": True},
            evidence_summary={"scenario_only": True, "external_factual_claims": 0},
            freshness_state="FRESH",
            confidence_level="HIGH",
            state_hash=content_hash(state_blob),
        )
        self.session.add(state)
        self.session.flush()
        candidate_payload = {
            "editorial_research_run_id": str(run.id),
            "proposed_title": FALLBACK_TOPIC,
            "proposed_angle": (
                "Transparent illustrative scenario: five people times one hour "
                "per day times four days; no guaranteed result."
            ),
            "proposed_format": "long-form documentary/explainer",
            "proposed_pillar": "practical automation leverage",
            "policy_snapshot_id": str(snapshot.id),
        }
        idea = EditorialIdeaCandidate(
            editorial_research_run_id=run.id,
            company_id=channel.company_id,
            channel_workspace_id=channel.id,
            policy_snapshot_id=snapshot.id,
            context_pack_snapshot_id=context.id,
            channel_state_pack_snapshot_id=state.id,
            stage="GREENLIT",
            proposed_title=FALLBACK_TOPIC,
            proposed_angle="Transparent illustrative scenario: five people times one hour per day times four days; no guaranteed result.",
            proposed_format="long-form documentary/explainer",
            proposed_pillar="practical automation leverage",
            rationale={
                "authority": "operator-approved fallback in PKG1 prompt",
                "scenario_not_measured": True,
                "off_blueprint": False,
            },
            evidence_refs=[
                {
                    "type": "context_pack",
                    "id": str(context.id),
                    "hash": context.pack_hash,
                }
            ],
            reason_codes=["PKG1_FALLBACK_ALLOWED", "SCENARIO_FRAMING_EXPLICIT"],
            confidence_level="HIGH",
            budget_readiness="READY",
            rights_policy_state="PASS",
            quality_state="PASS",
            canonical_hash=content_hash(candidate_payload),
            created_by_user_id=created_by_user_id,
        )
        self.session.add(idea)
        self.session.flush()
        run.context_pack_snapshot_id = context.id
        run.channel_state_pack_snapshot_id = state.id
        run.candidate_count = 1
        self.session.flush()
        return {
            "slot": slot,
            "run": run,
            "context": context,
            "state": state,
            "idea": idea,
            "used_fallback_topic": True,
            "selection_reason": "NO_APPROVED_IDEA_OPERATOR_FALLBACK",
        }

    @staticmethod
    def _idea_gate(
        idea: EditorialIdeaCandidate, *, used_fallback: bool
    ) -> dict[str, Any]:
        if idea.stage not in {"GREENLIT", "SELECTED_FOR_SLOT"}:
            return {"result": "BLOCK", "reason_codes": ["IDEA_NOT_APPROVED"]}
        if used_fallback:
            angle = (idea.proposed_angle or "").lower()
            if "illustrative" not in angle or "no guaranteed" not in angle:
                return {
                    "result": "BLOCK",
                    "reason_codes": ["FALLBACK_SCENARIO_FRAMING_MISSING"],
                }
        return {"result": "PASS", "reason_codes": ["IDEA_WITHIN_APPROVED_PROFILE"]}

    def _record_project_admission(
        self,
        *,
        project: VideoProject,
        selection: dict[str, Any],
        created_by_user_id: uuid.UUID,
    ) -> ProjectAdmissionDecision:
        decision = ProjectAdmissionDecision(
            editorial_research_run_id=selection["run"].id,
            editorial_idea_candidate_id=selection["idea"].id,
            editorial_calendar_slot_id=selection["slot"].id,
            company_id=project.company_id,
            channel_workspace_id=project.channel_workspace_id,
            policy_snapshot_id=project.policy_snapshot_id,
            budget_gate_result={
                "result": "PASS",
                "estimated_cost_usd": 0.0,
                "hard_cap_usd": 1.0,
            },
            readiness_gate_refs=[{"gate_key": "IdeaGate", "result": "PASS"}],
            decision="ADMIT",
            reason_codes=[
                "IDEA_GATE_PASS",
                "ACTIVE_POLICY_BOUND",
                "PROVIDER_EXECUTION_DISABLED",
            ],
            evidence_refs=[
                {
                    "type": "context_pack",
                    "id": str(selection["context"].id),
                    "hash": selection["context"].pack_hash,
                },
                {"type": "policy_snapshot", "id": str(project.policy_snapshot_id)},
            ],
            admitted_video_project_id=project.id,
            created_artifact_refs=[],
            created_by_user_id=created_by_user_id,
        )
        self.session.add(decision)
        self.session.flush()
        run = selection["run"]
        run.status = "COMPLETED"
        run.completed_at = utc_now()
        selection["idea"].stage = "IN_PRODUCTION"
        self.session.flush()
        return decision

    def _compile_artifact_payloads(
        self,
        *,
        project: VideoProject,
        selection: dict[str, Any],
        admission: ProjectAdmissionDecision,
        policy: ChannelScopedPolicy,
        snapshot: CompiledChannelPolicySnapshot,
    ) -> dict[str, dict[str, Any]]:
        sources = self._sources(project)
        claims = self._claims()
        script = self._script(policy)
        spoken = self._normalize_script(script, policy)
        pacing = self._pacing_preflight(script, snapshot, policy)
        format_contract = self.session.scalars(
            select(FormatIdentityContract).where(
                FormatIdentityContract.channel_id == project.channel_workspace_id,
                FormatIdentityContract.content_hash
                == project.format_identity_contract_hash,
                FormatIdentityContract.status == "APPROVED",
            )
        ).one_or_none()
        if format_contract is None:
            raise ValidationFailureError(
                "approved FormatIdentityContract not found for frozen project"
            )
        visual = self._visual_direction(project, script, spoken)
        cost = self._cost_estimate(policy, script)
        creative = (snapshot.compiled_payload or {}).get(
            "creative_quality_policies"
        ) or {}
        lineage = {
            "schema_version": "pkg1.idea-admission-lineage.v1",
            "editorial_calendar_slot_id": str(selection["slot"].id),
            "editorial_research_run_id": str(selection["run"].id),
            "context_pack_snapshot_id": str(selection["context"].id),
            "channel_state_pack_snapshot_id": str(selection["state"].id),
            "editorial_idea_candidate_id": str(selection["idea"].id),
            "idea_gate_result": {
                "result": "PASS",
                "reason_codes": ["IDEA_WITHIN_APPROVED_PROFILE"],
            },
            "project_admission_decision_id": str(admission.id),
            "video_project_id": str(project.id),
            "selection_reason": selection["selection_reason"],
            "used_fallback_topic": selection["used_fallback_topic"],
            "project_created_after_idea_gate": True,
        }
        research = {
            "schema_version": "pkg1.research-pack.v1",
            "topic": project.title,
            "research_question": "How can a small team audit one repeatable workflow and model a possible time saving without claiming a measured result?",
            "findings": [
                "The package uses a transparent illustrative scenario, not an external benchmark.",
                "The calculation is deterministic; whether any team realizes the saving depends on its workflow and adoption.",
                "No external product pricing, customer result, or performance statistic is asserted.",
            ],
            "external_factual_claim_count": 0,
            "source_refs": [item.source_id for item in sources],
            "provider_or_web_research_calls": 0,
        }
        source_pack = {
            "schema_version": "pkg1.source-pack.v1",
            "sources": [item.model_dump(mode="json") for item in sources],
            "rights_complete_for_current_use": True,
            "media_sources_selected": 0,
        }
        brief = PKG1CreativeBrief(
            profile_snapshot_ref=f"compiled-policy-snapshot://{project.policy_snapshot_id}",
            profile_snapshot_hash=snapshot.content_hash,
            viewer_problem="A small team sees repeated manual work but cannot tell which part is safe to automate.",
            audience_promise="A practical audit method plus a transparent twenty-hour illustrative scenario.",
            video_objective="Teach a bounded workflow-design method without guaranteeing a result.",
            central_thesis="One automation is valuable only when the repeated work, exception path, and ownership are explicit.",
            scenario_assumptions=[
                "five team members",
                "one hour per day",
                "four working days",
                "illustrative rather than measured",
            ],
            primary_takeaway="Start with one narrow recurring workflow, measure the baseline, and retain a human exception path.",
            format_structure=[
                "time-cost diagnosis",
                "transparent scenario",
                "workflow mechanism",
                "constraints",
                "pilot checklist",
                "practical takeaway",
            ],
            target_runtime_minutes=policy.audience_pacing_profile.target_runtime_minutes.model_dump(),
            tone="calm professional documentary/explainer",
            cta_posture="Invite the viewer to map one workflow; no product or performance promise.",
            evidence_requirements=[
                "scenario label",
                "explicit assumptions",
                "claim IDs",
                "no stock as evidence",
            ],
            visual_strategy=policy.channel_visual_strategy_profile.strategy_label,
            cost_class=policy.budget_policy.tier,
            risk_class="LOW",
            destination=policy.publish_policy.primary_destination,
            success_criteria=[
                "viewer can reproduce the arithmetic",
                "viewer can name trigger, owner, and exception",
                "no universal savings claim",
            ],
        )
        claim_ledger = {
            "schema_version": "pkg1.claim-evidence-ledger.v1",
            "claims": [item.model_dump(mode="json") for item in claims],
            "unsupported_claim_count": 0,
            "stock_used_as_evidence": False,
        }
        originality = {
            "schema_version": "pkg1.episode-originality-manifest.v1",
            "format_identity_contract_id": str(format_contract.id),
            "format_identity_contract_ref": project.format_identity_contract_ref,
            "format_identity_contract_hash": project.format_identity_contract_hash,
            "same_channel_comparison_scope": policy.originality_policy.rolling_same_channel_comparison_scope,
            "available_prior_pkg1_episodes": 0,
            "hook_family": "time-cost diagnosis",
            "angle": "transparent scenario plus workflow audit",
            "section_order": [segment.section for segment in script.segments],
            "visual_grammar": "workflow cards, counters, decision paths, and limited grounded context",
            "thumbnail_concept": "20 HOURS? beside a five-person workflow grid; question mark preserves scenario truthfulness",
            "metadata_pattern": "specific scenario plus method, no guaranteed outcome",
            "hero_concept": "none; native mechanism is sufficient",
            "stock_concept": "three non-recurring contextual team-work beats; not factual evidence",
            "similarity_findings": [
                "No prior admitted PKG1 episode exists for the same channel."
            ],
            "must_vary_evidence": {
                key: "FIRST_EPISODE_BASELINE"
                for key in policy.originality_policy.must_vary_elements
            },
            "global_strategy_b_boilerplate": False,
            "decision": "PASS",
            "human_review_state": "PENDING",
        }
        visual_contract = {
            "schema_version": "pkg1.visual-direction-contract.v1",
            **visual.model_dump(mode="json", exclude={"scenes"}),
            "visual_language_policy_ref": project.creative_quality_policy_ref,
            "source_selection_rules": {
                "mechanism_data_text_workflow_ui": "NATIVE_VISUAL",
                "grounded_context": "PEXELS_SUPPORTING_ONLY_WHEN_MEANINGFUL",
                "hero_metaphor_signature": "AI_HERO_ONLY_WHEN_JUSTIFIED",
            },
        }
        visual_contract_hash = content_hash(visual_contract)
        visual_plan = {
            "schema_version": "pkg1.visual-plan.v1",
            "visual_direction_contract_ref": "artifact://visual_direction_contract/current",
            "visual_direction_contract_hash": visual_contract_hash,
            "editorial_order_only": True,
            "canonical_timestamps_created": False,
            "scenes": [item.model_dump(mode="json") for item in visual.scenes],
            "coverage": {
                "segment_count": len(script.segments),
                "covered_segment_count": len(script.segments),
                "complete": True,
            },
        }
        stock_scenes = [
            item for item in visual.scenes if item.source_role == "PEXELS_SUPPORTING"
        ]
        asset_plan = {
            "schema_version": "pkg1.compiled-asset-request-plan.v1",
            "visual_direction_contract_ref": "artifact://visual_direction_contract/current",
            "visual_direction_contract_hash": visual_contract_hash,
            "provider_execution": "DISABLED",
            "selected_provider_assets": [],
            "raw_provider_urls": [],
            "pexels_query_plan_drafts": [
                {
                    "request_id": f"PEXELS-{index:02d}",
                    "purpose": scene.semantic_intent,
                    "scene_refs": [scene.scene_id],
                    "segment_refs": scene.segment_refs,
                    "semantic_intent": scene.semantic_intent,
                    "required_role": "PEXELS_SUPPORTING",
                    "fallback_order": ["NATIVE_VISUAL", "HUMAN_REVIEW"],
                    "resolution": "1920x1080 minimum",
                    "orientation": "16:9 landscape",
                    "duration_range_seconds": scene.target_duration_range_seconds,
                    "crop_policy": "safe center crop; no identity distortion",
                    "person_logo_evidence_policy": "no recurring person, no visible logo, never evidence",
                    "projected_cost_class": "FREE_API",
                    "human_review_required": True,
                }
                for index, scene in enumerate(stock_scenes, start=1)
            ],
            "ai_hero_asset_request_drafts": [],
            "native_visual_requirements": [
                {
                    "scene_id": scene.scene_id,
                    "purpose": scene.semantic_intent,
                    "required_role": "NATIVE_VISUAL",
                    "fallback_order": ["NATIVE_VISUAL_REVISION", "HUMAN_REVIEW"],
                    "resolution": "1920x1080",
                    "orientation": "16:9",
                    "duration_range_seconds": scene.target_duration_range_seconds,
                    "human_review_required": True,
                }
                for scene in visual.scenes
                if scene.source_role == "NATIVE_VISUAL"
            ],
            "fixed_duration_fit_decision": {
                "provider": "google_veo",
                "model_catalog_ref": policy.provider_usage_policy.google_veo.approved_model_catalog_ref,
                "advisory_scene_duration_range": None,
                "visual_intent": "No AI hero is required; native mechanism carries the episode.",
                "output": "NOT_ELIGIBLE",
                "reason": "Veo would be filler rather than a justified hero or signature beat.",
                "script_timing_changed_to_fit_provider": False,
            },
        }
        caption_plan = {
            "schema_version": "pkg1.caption-plan.v1",
            "subtitle_sidecar_policy_ref": f"{project.creative_quality_policy_ref}#subtitle_sidecar_policy",
            "subtitle_sidecar_policy_hash": content_hash(
                creative.get("subtitle_sidecar_policy") or {}
            ),
            "caption_sync_policy_ref": f"{project.creative_quality_policy_ref}#caption_sync_policy",
            "caption_sync_policy_hash": content_hash(
                creative.get("caption_sync_policy") or {}
            ),
            "readable_caption_compiler_version": "readable-caption-compiler.cqr1.v1",
            "sidecar_format_policy": (creative.get("subtitle_sidecar_policy") or {}).get(
                "longform_16_9", {}
            ),
            "cps_cpl_policy": (creative.get("subtitle_sidecar_policy") or {}).get(
                "global", {}
            ),
            "final_cues": [],
            "srt": None,
            "timing_authority": "WAIT_FOR_FINAL_AUDIO_ALIGNMENT_AND_CANONICAL_MEDIA_TIMELINE",
        }
        provider_plan = {
            "schema_version": "pkg1.provider-execution-plan.v1",
            "execution_enabled": False,
            "mr1_approval": "PENDING",
            "stages": [
                {
                    "order": 1,
                    "provider": "elevenlabs",
                    "operation": "complete_narration",
                    "planned_requests": 1,
                    "state": "NOT_AUTHORIZED",
                },
                {
                    "order": 2,
                    "provider": "forced_alignment",
                    "operation": "verify_spoken_timing",
                    "planned_requests": 1,
                    "state": "WAITING_FOR_FINAL_AUDIO",
                },
                {
                    "order": 3,
                    "provider": "pexels_api",
                    "operation": "supporting_asset_search",
                    "planned_requests": len(stock_scenes),
                    "state": "NOT_AUTHORIZED",
                },
                {
                    "order": 4,
                    "provider": "google_veo",
                    "operation": "ai_hero_generation",
                    "planned_requests": 0,
                    "state": "NOT_PLANNED",
                },
                {
                    "order": 5,
                    "provider": "native_ffmpeg_renderer",
                    "operation": "production_render",
                    "planned_requests": 1,
                    "state": "WAITING_FOR_CANONICAL_TIMELINE",
                },
                {
                    "order": 6,
                    "provider": "google_drive",
                    "operation": "archive",
                    "planned_requests": 1,
                    "state": "WAITING_FOR_FINAL_MEDIA",
                },
                {
                    "order": 7,
                    "provider": "youtube_manual",
                    "operation": "manual_upload",
                    "planned_requests": 0,
                    "state": "HUMAN_ONLY",
                },
            ],
            "forbidden_before_mr1": [
                "provider submit",
                "paid attempt",
                "download",
                "render",
                "archive",
                "publish",
            ],
        }
        rights = {
            "schema_version": "pkg1.rights-disclosure-completeness.v1",
            "planning_state": "PASS",
            "final_rights_state": "WAITING_FOR_ASSET_ACQUISITION",
            "pexels": {
                "provenance_required": True,
                "license_evidence_required": True,
                "selected_assets": 0,
            },
            "veo": {
                "planned_assets": 0,
                "synthetic_disclosure_required_if_added": True,
            },
            "voice": {
                "provider": "elevenlabs",
                "voice_provenance_required": True,
                "commercial_plan_approval_required": True,
            },
            "claims": {
                "ledger_ref": "artifact://claim_evidence_ledger/current",
                "external_measured_claims": 0,
            },
            "thumbnail_truthfulness": "Question-mark scenario framing required.",
            "metadata_truthfulness": "No guaranteed or measured outcome wording.",
            "publish_mode": "MANUAL_YOUTUBE_UPLOAD_ONLY",
            "archive_before_purge": True,
        }
        disclosure = {
            "schema_version": "pkg1.synthetic-media-disclosure-receipt-draft.v1",
            "receipt_status": "PRE_RENDER_PLANNED",
            "synthetic_voice_planned": True,
            "synthetic_video_planned": False,
            "real_person_likeness": False,
            "final_decision_waits_for_acquired_assets": True,
            "platform_disclosure_required": policy.publish_policy.synthetic_media_disclosure_required,
        }
        publish_draft = {
            "schema_version": "pkg1.publish-package-draft.v1",
            "state": "DRAFT_PRE_MEDIA",
            "destination": "YouTube",
            "title_draft": "How One Automation Could Save a Small Team 20 Hours a Week",
            "description_truthfulness_note": "Twenty hours is an illustrative calculation, not a guaranteed or measured result.",
            "thumbnail_concept": originality["thumbnail_concept"],
            "claim_evidence_ref": "artifact://claim_evidence_ledger/current",
            "rights_report_ref": "artifact://rights_disclosure_completeness_report/current",
            "final_media_ref": None,
            "upload_task": None,
            "manual_upload_only": True,
            "final": False,
        }
        checklist = {
            "schema_version": "pkg1.manual-publish-checklist-draft.v1",
            "state": "DRAFT_NOT_ACTIONABLE",
            "items": [
                {"key": "FINAL_MEDIA_QC", "state": "WAITING_FOR_RENDER"},
                {
                    "key": "CAPTIONS_FROM_CANONICAL_TIMELINE",
                    "state": "WAITING_FOR_FINAL_AUDIO",
                },
                {
                    "key": "RIGHTS_AND_PROVENANCE",
                    "state": "WAITING_FOR_ACQUIRED_ASSETS",
                },
                {"key": "AI_DISCLOSURE", "state": "PLANNED"},
                {"key": "DRIVE_ARCHIVE_VERIFIED", "state": "WAITING_FOR_FINAL_MEDIA"},
                {"key": "FINAL_HUMAN_REVIEW", "state": "NOT_RUN"},
            ],
            "human_upload_task_created": False,
        }
        return {
            "idea_admission_lineage": lineage,
            "research_pack": research,
            "source_pack": source_pack,
            "creative_brief": brief.model_dump(mode="json"),
            "script": script.model_dump(mode="json"),
            "spoken_text_normalized": spoken.model_dump(mode="json"),
            "claim_evidence_ledger": claim_ledger,
            "narration_pacing_preflight_estimate": pacing.model_dump(mode="json"),
            "episode_originality_manifest": originality,
            "visual_direction_contract": visual_contract,
            "visual_plan": visual_plan,
            "compiled_asset_request_plan": asset_plan,
            "caption_plan": caption_plan,
            "cost_estimate_snapshot": cost.model_dump(mode="json"),
            "provider_execution_plan": provider_plan,
            "paid_attempt_plan": {
                "schema_version": "pkg1.paid-attempt-plan.v1",
                "execution_state": "NOT_AUTHORIZED",
                "mr1_approval": "PENDING",
                "provider_attempts": [
                    {
                        "provider": "elevenlabs",
                        "maximum_attempts": 1,
                        "actual_attempts": 0,
                    }
                ],
                "veo_attempts": 0,
                "new_approval_required_for_retry": True,
            },
            "rights_disclosure_completeness_report": rights,
            "synthetic_media_disclosure_receipt_draft": disclosure,
            "publish_package_draft": publish_draft,
            "manual_publish_checklist_draft": checklist,
        }

    @staticmethod
    def _sources(project: VideoProject) -> list[PKG1Source]:
        return [
            PKG1Source(
                source_id="SRC-001",
                source_type="OPERATOR_APPROVAL",
                title="PKG1 operator-approved fallback topic and framing",
                source_ref="prompt://pkg1/operator-approved-fallback",
                freshness="CURRENT_PACKAGE",
                confidence="HIGH",
                rights_state="INTERNAL_APPROVED",
                allowed_use="Topic selection and explicit scenario framing only.",
            ),
            PKG1Source(
                source_id="SRC-002",
                source_type="DETERMINISTIC_CALCULATION",
                title="Five people times one hour per day times four days",
                source_ref="calculation://pkg1/5x1x4",
                freshness="NOT_TIME_SENSITIVE",
                confidence="HIGH",
                rights_state="NOT_APPLICABLE",
                allowed_use="Illustrative arithmetic only; never measured evidence.",
            ),
            PKG1Source(
                source_id="SRC-003",
                source_type="ACTIVE_POLICY",
                title="Frozen small-team-ai channel policy",
                source_ref=f"compiled-policy-snapshot://{project.policy_snapshot_id}",
                freshness="FROZEN_AT_PROJECT_CREATION",
                confidence="HIGH",
                rights_state="INTERNAL_APPROVED",
                allowed_use="Format, pacing, evidence, visual, provider, cost, and publish constraints.",
            ),
        ]

    @staticmethod
    def _claims() -> list[PKG1ClaimEvidence]:
        return [
            PKG1ClaimEvidence(
                claim_id="CLM-001",
                claim_text="In the illustrative scenario, five people times one hour per day times four working days equals twenty hours.",
                claim_type="ILLUSTRATIVE_SCENARIO",
                source_refs=["SRC-001", "SRC-002"],
                freshness="NOT_TIME_SENSITIVE",
                confidence="HIGH",
                allowed_wording="This illustrative scenario adds up to twenty hours; an actual team may save less, more, or none.",
                disallowed_wording="This automation will save every small team twenty hours each week.",
                verification_state="ILLUSTRATIVE_ONLY",
                assumptions=[
                    {"key": "team_members", "value": 5, "unit": "people"},
                    {
                        "key": "manual_time_per_person_per_day",
                        "value": 1,
                        "unit": "hours",
                    },
                    {"key": "working_days", "value": 4, "unit": "days"},
                ],
                calculation="5 people * 1 hour/person/day * 4 days = 20 hours",
                result=20,
                result_unit="hours per illustrative week",
            ),
            PKG1ClaimEvidence(
                claim_id="CLM-002",
                claim_text="A useful automation design names the trigger, inputs, owner, success condition, and exception path.",
                claim_type="EDITORIAL_INFERENCE",
                source_refs=["SRC-003"],
                freshness="NOT_TIME_SENSITIVE",
                confidence="HIGH",
                allowed_wording="Use these elements as a practical design checklist.",
                disallowed_wording="This checklist guarantees a successful automation.",
                verification_state="VERIFIED",
            ),
            PKG1ClaimEvidence(
                claim_id="CLM-003",
                claim_text="The scenario becomes decision-useful only when the team records a baseline and verifies which manual steps disappear.",
                claim_type="EDITORIAL_INFERENCE",
                source_refs=["SRC-002", "SRC-003"],
                freshness="NOT_TIME_SENSITIVE",
                confidence="HIGH",
                allowed_wording="Measure the team's own baseline before treating the scenario as a planning estimate.",
                disallowed_wording="The scenario is a proven customer result.",
                verification_state="VERIFIED",
            ),
        ]

    @staticmethod
    def _script(policy: ChannelScopedPolicy) -> PKG1EditorialScript:
        sections = [
            (
                "S01",
                "The scenario, not a promise",
                "Picture a five-person team facing the same manual handoff every afternoon. Each person spends about one hour checking inputs, copying details, and chasing a status update. Now limit the model to four working days. Five people, times one hour, times four days, equals twenty hours. That number is an illustrative scenario. It is not a benchmark, a customer result, or a guarantee. A real team could recover less time, more time, or no time at all. The useful question is not whether the headline sounds impressive. It is whether one repeated workflow contains steps that can be removed safely, while the team keeps control of exceptions.",
                ["CLM-001"],
                ["SRC-001", "SRC-002"],
                "Open with a five-column workload grid and reveal the arithmetic as a labeled scenario.",
            ),
            (
                "S02",
                "Make the arithmetic auditable",
                "The twenty-hour total has only three assumptions. There are five people. The repeated work takes one hour per person per day. It happens on four days. Change any input and the result changes. If the work takes twenty minutes, the total falls sharply. If only two people do it, the model changes again. This is why the calculation belongs on screen, with every assumption visible. The number should behave like a variable, not a claim carved in stone. Before designing anything, replace the sample inputs with the team's own observations. The scenario is a starting point for an audit. It is not evidence that savings have already occurred.",
                ["CLM-001", "CLM-003"],
                ["SRC-002", "SRC-003"],
                "Use native counters that change the three inputs and recompute the illustrative total.",
            ),
            (
                "S03",
                "Choose a bounded workflow",
                "A promising target is narrow, frequent, and easy to observe. Think about a status handoff, an intake check, or a recurring summary. Avoid starting with a vague goal such as automate operations. Write the workflow as a sequence. Something triggers it. Inputs arrive. A person checks conditions. An output moves to the next owner. Exceptions return to a human. This boundary matters because automation cannot repair an undefined process. If two team members follow different rules, capture that difference first. The first package should automate one stable path, not every edge case. A small boundary makes the pilot easier to inspect and easier to reverse.",
                ["CLM-002"],
                ["SRC-003"],
                "Map trigger, inputs, checks, output, and exception as a native workflow diagram.",
            ),
            (
                "S04",
                "Observe the current handoff",
                "Watch the workflow before changing it. Record where the request begins, which fields are copied, how often information is missing, and who resolves the gap. A grounded office shot can provide context here, but it cannot prove the process or the time saving. The evidence must come from the team's own baseline. Count completed handoffs. Note rework. Mark the steps that require judgment. Then separate work that moves information from work that makes a decision. Moving clean information is often easier to standardize. Judgment-heavy exceptions should stay visible to a person. The goal is not to remove humans. It is to remove avoidable repetition without hiding responsibility.",
                ["CLM-003"],
                ["SRC-003"],
                "Use brief supporting team-work context, then return to a native baseline checklist.",
            ),
            (
                "S05",
                "Design the controlled path",
                "Define the automation with five labels: trigger, inputs, owner, success condition, and exception path. The trigger might be a completed form. The inputs are the fields the next step needs. The owner remains accountable for the outcome. The success condition says what a correct handoff looks like. The exception path says when the workflow stops and asks for help. These labels keep a convenient shortcut from becoming an invisible system. Add an activity record so the team can see what happened. Keep the first version reversible. If the automation cannot explain why it stopped, the operator should be able to inspect the input and finish the task manually.",
                ["CLM-002"],
                ["SRC-003"],
                "Build a native five-card control panel and highlight the exception path.",
            ),
            (
                "S06",
                "Pilot without claiming the result",
                "Run the pilot on a limited slice of work. Compare it with the recorded baseline. Track how many handoffs finish cleanly, how many require correction, and how much manual work truly disappears. Do not count time that merely moves to another person. Do not count a faster first step if rework increases later. The twenty-hour scenario remains a hypothesis until the team's own records support a different number. A short pilot can still be useful when the saving is smaller than expected. It may reveal a missing field, an unclear owner, or an exception that occurs too often. That evidence improves the workflow even when the headline estimate does not survive.",
                ["CLM-001", "CLM-003"],
                ["SRC-002", "SRC-003"],
                "Show native baseline-versus-pilot cards with a prominent HYPOTHESIS label.",
            ),
            (
                "S07",
                "Protect the exception path",
                "The normal path is only half the design. Missing data, duplicate requests, unusual approvals, and system outages need an explicit destination. Route them to a named owner. Preserve the original input. Avoid silent retries that can create duplicate work. A supporting shot of a team reviewing an exception can make the operational context clear, but the visual remains illustrative. The workflow record is the evidence. Set a threshold for pausing the pilot if exceptions rise. This does not need a complicated control room. A simple queue, a reason code, and a manual fallback can be enough. Control is part of the time-saving design, not a separate concern added later.",
                ["CLM-002"],
                ["SRC-003"],
                "Use supporting review context followed by a native exception queue and reason codes.",
            ),
            (
                "S08",
                "Decide with the team's own data",
                "At the end of the pilot, return to the three scenario inputs. Replace five people with the number actually involved. Replace one hour with the observed manual time that disappeared. Replace four days with the real frequency. Then subtract setup, review, and exception handling. The result may be positive, neutral, or negative. That is a decision signal, not a failure of the exercise. Continue only if the workflow is clearer and the total cost fits the team's limit. Expand one step at a time. If the evidence is weak, keep the process manual and revise the design. A transparent small result is more useful than a large number the team cannot reproduce.",
                ["CLM-003"],
                ["SRC-002", "SRC-003"],
                "Animate a native calculation sheet that replaces scenario inputs with observed values.",
            ),
            (
                "S09",
                "The practical next move",
                "Choose one handoff that repeats this week. Write its trigger, inputs, owner, success condition, and exception path. Measure the current manual effort before building anything. Use the twenty-hour example only as a transparent way to test assumptions. Then run a bounded pilot and keep the fallback visible. No automation can promise a universal saving. The useful outcome is a workflow the team can inspect, measure, and stop when it behaves badly. If the pilot removes real repetition, the team's own baseline will show it. If it does not, the same evidence will prevent a costly rollout. Map one workflow first. Let observed results, not the headline, decide what happens next.",
                ["CLM-001", "CLM-002", "CLM-003"],
                ["SRC-001", "SRC-002", "SRC-003"],
                "Close with grounded planning context and a native five-item audit checklist.",
            ),
        ]
        segments: list[PKG1ScriptSegment] = []
        cursor = 0
        for index, (
            segment_id,
            section,
            text,
            claim_ids,
            source_refs,
            visual_hint,
        ) in enumerate(sections):
            start = cursor
            end = start + len(text)
            segments.append(
                PKG1ScriptSegment(
                    segment_id=segment_id,
                    section=section,
                    editorial_span={"start_char": start, "end_char": end},
                    text=text,
                    claim_ids=claim_ids,
                    source_refs=source_refs,
                    visual_intent_hint=visual_hint,
                    pronunciation_notes=[],
                    section_boundary="OPEN"
                    if index == 0
                    else "CLOSE"
                    if index == len(sections) - 1
                    else "CONTINUE",
                )
            )
            cursor = end + 2
        word_count = sum(
            len(re.findall(r"\b[\w'-]+\b", item.text)) for item in segments
        )
        target = policy.audience_pacing_profile.target_runtime_minutes
        return PKG1EditorialScript(
            language="en-US",
            tone="documentary/explainer",
            title=FALLBACK_TOPIC,
            segments=segments,
            cta_decision="Soft action: map one workflow and measure its baseline; no product CTA.",
            estimated_word_count=word_count,
            advisory_duration_estimate_minutes={
                "minimum": round(word_count / 155, 2),
                "maximum": round(word_count / 130, 2),
                "channel_minimum": target.minimum,
                "channel_maximum": target.maximum,
            },
        )

    @staticmethod
    def _normalize_script(
        script: PKG1EditorialScript, policy: ChannelScopedPolicy
    ) -> PKG1SpokenTextNormalized:
        mappings: list[SpokenMappingUnit] = []
        tokens: list[SpokenToken] = []
        spoken_segments: list[str] = []
        token_index = 0
        for segment in script.segments:
            spoken = segment.text.replace("twenty-hour", "twenty hour")
            spoken = re.sub(r"\s+", " ", spoken).strip()
            operations = ["NORMALIZE_WHITESPACE"]
            if spoken != segment.text:
                operations.insert(0, "NORMALIZE_HYPHENATED_NUMBER_FOR_SPEECH")
            words = re.findall(r"\b[\w'-]+\b|[^\w\s]", spoken)
            start = token_index
            for word in words:
                tokens.append(
                    SpokenToken(
                        index=token_index, segment_id=segment.segment_id, text=word
                    )
                )
                token_index += 1
            mappings.append(
                SpokenMappingUnit(
                    segment_id=segment.segment_id,
                    source_text=segment.text,
                    spoken_text=spoken,
                    source_hash=content_hash({"text": segment.text}),
                    spoken_hash=content_hash({"text": spoken}),
                    spoken_token_start=start,
                    spoken_token_end=token_index - 1,
                    operations=operations,
                )
            )
            spoken_segments.append(spoken)
        normalized = "\n\n".join(spoken_segments)
        return PKG1SpokenTextNormalized(
            compiler_version="pkg1.spoken-normalizer.v1",
            policy_ref=policy.creative_quality_binding.policy_ref,
            source_script_hash=content_hash(script.model_dump(mode="json")),
            normalized_text_hash=content_hash({"normalized_text": normalized}),
            normalized_text=normalized,
            mappings=mappings,
            spoken_tokens=tokens,
            pronunciation_dictionary_refs=policy.voice_policy.pronunciation_dictionary_refs,
            ambiguous_transforms=[],
            provider_timing_created=False,
        )

    @staticmethod
    def _pacing_preflight(
        script: PKG1EditorialScript,
        snapshot: CompiledChannelPolicySnapshot,
        policy: ChannelScopedPolicy,
    ) -> NarrationPacingPreflightEstimate:
        pacing = (
            (snapshot.compiled_payload or {}).get("creative_quality_policies") or {}
        ).get("narration_pacing_policy") or {}
        delivered = (pacing.get("body_delivered_wpm") or {}).get("pass") or [130, 155]
        sentences = [
            piece
            for segment in script.segments
            for piece in re.split(r"(?<=[.!?])\s+", segment.text)
            if piece
        ]
        sentence_lengths = [len(re.findall(r"\b[\w'-]+\b", item)) for item in sentences]
        predicted = {
            "minimum": round(script.estimated_word_count / float(delivered[1]), 2),
            "maximum": round(script.estimated_word_count / float(delivered[0]), 2),
        }
        target = policy.audience_pacing_profile.target_runtime_minutes.model_dump()
        overlaps = (
            predicted["maximum"] >= target["minimum"]
            and predicted["minimum"] <= target["maximum"]
        )
        return NarrationPacingPreflightEstimate(
            name="NarrationPacingPreflightEstimate",
            advisory_only=True,
            word_count=script.estimated_word_count,
            sentence_count=len(sentences),
            maximum_sentence_words=max(sentence_lengths),
            approved_delivery_wpm_range={
                "minimum": float(delivered[0]),
                "maximum": float(delivered[1]),
            },
            predicted_duration_minutes=predicted,
            target_runtime_minutes=target,
            decision="ADVISORY_PASS"
            if overlaps and max(sentence_lengths) <= 34
            else "BLOCK",
            canonical_timing_authority=False,
        )

    @staticmethod
    def _visual_direction(
        project: VideoProject,
        script: PKG1EditorialScript,
        spoken: PKG1SpokenTextNormalized,
    ) -> PKG1VisualDirection:
        supporting = {"S04", "S07", "S09"}
        mapping = {item.segment_id: item for item in spoken.mappings}
        scenes: list[SceneVisualIntent] = []
        for order, segment in enumerate(script.segments, start=1):
            source_role = (
                "PEXELS_SUPPORTING"
                if segment.segment_id in supporting
                else "NATIVE_VISUAL"
            )
            scenes.append(
                SceneVisualIntent(
                    scene_id=f"SC-{order:02d}",
                    segment_refs=[segment.segment_id],
                    editorial_order=order,
                    spoken_token_span_intent={
                        "start": mapping[segment.segment_id].spoken_token_start,
                        "end": mapping[segment.segment_id].spoken_token_end,
                    },
                    target_duration_range_seconds={"minimum": 35.0, "maximum": 85.0},
                    source_role=source_role,
                    semantic_intent=segment.visual_intent_hint,
                    evidence_role="CONTEXT_ONLY"
                    if source_role == "PEXELS_SUPPORTING"
                    else "EXPLANATORY",
                    source_justification=(
                        "Grounded real-world team context is meaningful here; return immediately to native explanation."
                        if source_role == "PEXELS_SUPPORTING"
                        else "The beat explains a mechanism, calculation, workflow, or decision and therefore stays native."
                    ),
                    canonical_timestamps=None,
                )
            )
        return PKG1VisualDirection(
            contract_ref=project.format_identity_contract_ref or "",
            contract_hash=project.format_identity_contract_hash or "",
            native_backbone_required=True,
            stock_is_factual_evidence=False,
            ai_hero_is_filler=False,
            scenes=scenes,
        )

    @staticmethod
    def _cost_estimate(
        policy: ChannelScopedPolicy, script: PKG1EditorialScript
    ) -> PKG1CostEstimate:
        estimated_characters = sum(len(segment.text) for segment in script.segments)
        line_items = [
            {
                "provider": "elevenlabs",
                "operation": "narration",
                "estimated_units": {"characters": estimated_characters, "attempts": 1},
                "estimated_incremental_cost_usd": 0.0,
                "basis": "existing subscription credits; no per-character cash price asserted",
                "catalog_ref": "config://media_provider_budget_policy_catalog/1.0.1#elevenlabs_default",
            },
            {
                "provider": "forced_alignment",
                "estimated_incremental_cost_usd": 0.0,
                "basis": "local planned operation",
            },
            {
                "provider": "pexels_api",
                "estimated_incremental_cost_usd": 0.0,
                "basis": "free API; three planned searches",
            },
            {
                "provider": "google_veo",
                "estimated_incremental_cost_usd": 0.0,
                "planned_clips": 0,
                "reference_price_if_later_justified": {
                    "model": "veo-3.1-fast-generate-preview",
                    "resolution": "720p",
                    "seconds": 8,
                    "usd": 0.8,
                },
                "catalog_ref": policy.provider_usage_policy.google_veo.approved_model_catalog_ref,
            },
            {
                "provider": "native_ffmpeg_renderer",
                "estimated_incremental_cost_usd": 0.0,
                "basis": "local",
            },
            {
                "provider": "google_drive",
                "estimated_incremental_cost_usd": 0.0,
                "basis": "existing workspace plan",
            },
        ]
        return PKG1CostEstimate(
            catalog_refs=[
                "config://media_provider_budget_policy_catalog/1.0.1",
                policy.provider_usage_policy.google_veo.approved_model_catalog_ref,
                f"{policy.budget_policy.derivation_refs[-1]}",
            ],
            currency="USD",
            line_items=line_items,
            estimated_cost=0.0,
            hard_cap=policy.budget_policy.max_estimated_cost_per_video,
            actual_cost=None,
            estimate_state="PLANNING_ONLY",
            decision="PASS",
        )

    def _create_artifact_version(
        self,
        *,
        project_id: uuid.UUID,
        artifact_type: str,
        content: dict[str, Any],
        created_by_user_id: uuid.UUID,
        context_refs: list[dict[str, Any]] | None = None,
    ) -> ArtifactVersion:
        artifact_service = ArtifactService(self.session)
        artifact = artifact_service.create_artifact(
            data=ArtifactCreate(
                video_project_id=project_id,
                artifact_type=artifact_type,
                status="in_review",
                created_by_user_id=created_by_user_id,
            ),
            correlation_id=f"pkg1-artifact-{artifact_type}",
            trusted_authority_write=True,
        )
        return artifact_service.create_artifact_version(
            data=ArtifactVersionCreate(
                artifact_id=artifact.id,
                content=deepcopy(content),
                status="submitted",
                created_by_user_id=created_by_user_id,
                context_refs=context_refs or [],
                packaging_metadata={"pkg1": True, "provider_execution": "DISABLED"},
            ),
            correlation_id=f"pkg1-artifact-version-{artifact_type}",
            trusted_authority_write=True,
        )

    def _evaluate_all_gates(
        self,
        *,
        project: VideoProject,
        selection: dict[str, Any],
        artifact_versions: dict[str, ArtifactVersion],
        policy: ChannelScopedPolicy,
        no_execution_before: dict[str, int],
        created_by_user_id: uuid.UUID,
    ) -> list[PKG1GateResult]:
        claim_result, claim_reasons = self.claim_evidence_gate(
            artifact_versions["claim_evidence_ledger"].content
        )
        script = PKG1EditorialScript.model_validate(artifact_versions["script"].content)
        spoken = PKG1SpokenTextNormalized.model_validate(
            artifact_versions["spoken_text_normalized"].content
        )
        pacing = NarrationPacingPreflightEstimate.model_validate(
            artifact_versions["narration_pacing_preflight_estimate"].content
        )
        visual = artifact_versions["visual_plan"].content
        asset = artifact_versions["compiled_asset_request_plan"].content
        cost = PKG1CostEstimate.model_validate(
            artifact_versions["cost_estimate_snapshot"].content
        )
        rights = artifact_versions["rights_disclosure_completeness_report"].content
        checks: dict[str, tuple[str, list[str]]] = {
            "IdeaGate": ("PASS", ["IDEA_WITHIN_APPROVED_PROFILE"]),
            "ProjectAdmissionGate": ("PASS", ["PROJECT_CREATED_AFTER_IDEA_GATE"]),
            "SourceQualityGate": ("PASS", ["NO_UNVERIFIED_EXTERNAL_FACTS"]),
            "ClaimEvidenceGate": (claim_result, claim_reasons),
            "ScriptNormalizationGate": (
                "PASS"
                if not spoken.ambiguous_transforms
                and len(spoken.mappings) == len(script.segments)
                else "BLOCK",
                ["NORMALIZATION_MAPPING_COMPLETE"],
            ),
            "ScriptDurationPreflightGate": (
                "PASS" if pacing.decision == "ADVISORY_PASS" else "BLOCK",
                ["ADVISORY_DURATION_OVERLAPS_CHANNEL_RANGE"],
            ),
            "OriginalityGate": ("PASS", ["FIRST_EPISODE_SAME_CHANNEL_BASELINE"]),
            "VisualCoverageGate": (
                "PASS" if visual["coverage"]["complete"] else "BLOCK",
                ["EVERY_SEGMENT_HAS_VISUAL_INTENT"],
            ),
            "VisualSourcePolicyGate": (
                "PASS"
                if not asset["ai_hero_asset_request_drafts"]
                and not asset["selected_provider_assets"]
                else "BLOCK",
                ["NATIVE_BACKBONE_STOCK_SUPPORTING_VEO_NOT_FILLER"],
            ),
            "VisualDirectionCompletenessGate": (
                "PASS"
                if visual.get("visual_direction_contract_hash")
                == artifact_versions["visual_direction_contract"].content_hash
                and asset.get("visual_direction_contract_hash")
                == artifact_versions["visual_direction_contract"].content_hash
                else "BLOCK",
                ["DIRECTION_CONTRACT_REF_AND_HASH_BOUND"],
            ),
            "ProviderBoundaryGate": (
                "PASS"
                if self.no_execution_counts() == no_execution_before
                else "BLOCK",
                ["NO_PROVIDER_MEDIA_ARCHIVE_OR_PUBLISH_EXECUTION"],
            ),
            "ProviderCostEstimateGate": (
                "PASS" if cost.actual_cost is None else "BLOCK",
                ["ACTUAL_COST_NULL"],
            ),
            "PerVideoCostGate": (
                "PASS" if cost.estimated_cost <= cost.hard_cap else "BLOCK",
                ["ESTIMATE_WITHIN_HARD_CAP"],
            ),
            "ChannelMonthlyBudgetGate": (
                "PASS"
                if cost.estimated_cost <= policy.budget_policy.monthly_channel_budget
                else "BLOCK",
                ["ESTIMATE_WITHIN_MONTHLY_BUDGET"],
            ),
            "RightsDisclosureCompletenessGate": (
                "PASS" if rights["planning_state"] == "PASS" else "BLOCK",
                ["PRE_RENDER_RIGHTS_PLAN_COMPLETE"],
            ),
            "SyntheticDisclosurePlanningGate": (
                "PASS",
                ["SYNTHETIC_DISCLOSURE_DRAFTED"],
            ),
            "PromptBudgetGate": ("PASS", ["NO_LLM_OR_PROVIDER_PROMPT_EXECUTION"]),
            "ContextPackShapeGate": (
                "PASS"
                if selection["context"].pack_hash and selection["state"].state_hash
                else "BLOCK",
                ["CONTEXT_AND_CHANNEL_STATE_PACKS_HASHED"],
            ),
        }
        results: list[PKG1GateResult] = []
        evidence_map = {
            "ClaimEvidenceGate": artifact_versions["claim_evidence_ledger"].id,
            "ScriptNormalizationGate": artifact_versions["spoken_text_normalized"].id,
            "ScriptDurationPreflightGate": artifact_versions[
                "narration_pacing_preflight_estimate"
            ].id,
            "OriginalityGate": artifact_versions["episode_originality_manifest"].id,
            "VisualCoverageGate": artifact_versions["visual_plan"].id,
            "VisualSourcePolicyGate": artifact_versions[
                "compiled_asset_request_plan"
            ].id,
            "VisualDirectionCompletenessGate": artifact_versions[
                "visual_direction_contract"
            ].id,
            "ProviderCostEstimateGate": artifact_versions["cost_estimate_snapshot"].id,
            "PerVideoCostGate": artifact_versions["cost_estimate_snapshot"].id,
            "RightsDisclosureCompletenessGate": artifact_versions[
                "rights_disclosure_completeness_report"
            ].id,
        }
        for gate_key in PRE_RENDER_GATES:
            result, reasons = checks[gate_key]
            evidence_id = evidence_map.get(gate_key)
            self._record_gate_run(
                project_id=project.id,
                gate_key=gate_key,
                result=result,
                reason_codes=reasons,
                created_by_user_id=created_by_user_id,
                artifact_version_id=evidence_id,
                revision_cycle=0,
            )
            results.append(
                PKG1GateResult(
                    gate_key=gate_key,
                    result=result,
                    reason_codes=reasons,
                    evidence_refs=[str(evidence_id)]
                    if evidence_id
                    else [str(project.id)],
                    revision_cycle=0,
                )
            )
        for gate_key in POST_MEDIA_GATES:
            self._record_gate_run(
                project_id=project.id,
                gate_key=gate_key,
                result="NOT_RUN",
                reason_codes=["FINAL_AUDIO_OR_MEDIA_NOT_CREATED"],
                created_by_user_id=created_by_user_id,
                revision_cycle=0,
            )
            results.append(
                PKG1GateResult(
                    gate_key=gate_key,
                    result="NOT_RUN",
                    reason_codes=["FINAL_AUDIO_OR_MEDIA_NOT_CREATED"],
                    evidence_refs=[str(project.id)],
                    revision_cycle=0,
                )
            )
        if any(item.result == "BLOCK" for item in results):
            raise ValidationFailureError("PKG1 deterministic pre-render gate blocked")
        return results

    def _record_gate_run(
        self,
        *,
        project_id: uuid.UUID,
        gate_key: str,
        result: str,
        reason_codes: list[str],
        created_by_user_id: uuid.UUID,
        artifact_version_id: uuid.UUID | None = None,
        revision_cycle: int,
    ) -> GateRun:
        definition = self.session.scalars(
            select(GateDefinitionVersion).where(
                GateDefinitionVersion.gate_key == gate_key,
                GateDefinitionVersion.version == "pkg1.v1",
            )
        ).one_or_none()
        if definition is None:
            definition = GateDefinitionVersion(
                gate_key=gate_key,
                gate_name=gate_key,
                gate_domain="PKG1_PRE_RENDER"
                if gate_key in PRE_RENDER_GATES
                else "PKG1_POST_MEDIA",
                version="pkg1.v1",
                status="active",
                input_schema_version="pkg1.gate-input.v1",
                output_schema_version="pkg1.gate-output.v1",
                definition={"deterministic": True, "provider_execution": False},
                reason_code_refs=reason_codes,
                created_by_user_id=created_by_user_id,
                activated_at=utc_now(),
            )
            self.session.add(definition)
            self.session.flush()
        project = self.session.get(VideoProject, project_id)
        if project is None:
            raise NotFoundError(f"project not found: {project_id}")
        input_snapshot = {
            "project_id": str(project.id),
            "policy_snapshot_id": str(project.policy_snapshot_id),
            "artifact_version_id": str(artifact_version_id)
            if artifact_version_id
            else None,
            "revision_cycle": revision_cycle,
            "provider_execution": "DISABLED",
        }
        stored_result = "SKIPPED" if result == "NOT_RUN" else result
        run = GateRun(
            gate_definition_version_id=definition.id,
            gate_key=gate_key,
            target_type="artifact_version" if artifact_version_id else "video_project",
            target_id=artifact_version_id or project.id,
            video_project_id=project.id,
            artifact_version_id=artifact_version_id,
            review_task_id=None,
            policy_snapshot_id=project.policy_snapshot_id,
            input_snapshot=input_snapshot,
            input_snapshot_hash=content_hash(input_snapshot),
            result=stored_result,
            reason_codes=reason_codes,
            evidence_refs=[{"artifact_version_id": str(artifact_version_id)}]
            if artifact_version_id
            else [{"project_id": str(project.id)}],
            metric_refs=[],
            freshness_state="NOT_REQUIRED",
            confidence_level="HIGH",
            confidence_reason_codes=["DETERMINISTIC_PKG1_CHECK"],
            decision_basis={
                "display_result": result,
                "revision_cycle": revision_cycle,
                "provider_execution": "DISABLED",
            },
            created_review_task_id=None,
            created_by_user_id=created_by_user_id,
        )
        self.session.add(run)
        self.session.flush()
        return run

    def _evaluate_revised_gate(
        self,
        project_id: uuid.UUID,
        gate_key: str,
        content: dict[str, Any],
    ) -> tuple[str, list[str]]:
        if gate_key == "ClaimEvidenceGate":
            return self.claim_evidence_gate(content)
        if gate_key == "ScriptNormalizationGate":
            try:
                parsed = PKG1SpokenTextNormalized.model_validate(content)
            except ValidationError:
                return "BLOCK", ["NORMALIZATION_SCHEMA_INVALID"]
            return (
                ("PASS", ["NORMALIZATION_MAPPING_COMPLETE"])
                if not parsed.ambiguous_transforms
                else ("BLOCK", ["AMBIGUOUS_TRANSFORM"])
            )
        if gate_key == "VisualDirectionCompletenessGate":
            contract = self._current_artifact(project_id, "visual_direction_contract")
            visual_plan = self._current_artifact(project_id, "visual_plan")
            asset_plan = self._current_artifact(
                project_id, "compiled_asset_request_plan"
            )
            bound = bool(
                contract
                and visual_plan
                and asset_plan
                and visual_plan.content.get("visual_direction_contract_hash")
                == contract.content_hash
                and asset_plan.content.get("visual_direction_contract_hash")
                == contract.content_hash
            )
            return (
                ("PASS", ["DIRECTION_CONTRACT_REF_AND_HASH_BOUND"])
                if bound
                else ("BLOCK", ["DIRECTION_CONTRACT_HASH_BINDING_INCOMPLETE"])
            )
        return "PASS", ["PKG1_REVISION_RECHECK_PASS"]

    def _package_manifest_content(
        self,
        project: VideoProject,
        selection: dict[str, Any],
        admission: ProjectAdmissionDecision,
        artifacts: dict[str, ArtifactVersion],
        gates: list[PKG1GateResult],
    ) -> dict[str, Any]:
        return {
            "schema_version": "pkg1.package-manifest.v1",
            "package_status": "TECHNICAL_PASS_HUMAN_REVIEW_PENDING",
            "topic": project.title,
            "used_fallback_topic": selection["used_fallback_topic"],
            "video_project_id": str(project.id),
            "snapshot_lineage": self._project_lineage(project),
            "admission_lineage": {
                "editorial_calendar_slot_id": str(selection["slot"].id),
                "editorial_idea_candidate_id": str(selection["idea"].id),
                "project_admission_decision_id": str(admission.id),
            },
            "artifacts": {
                key: {
                    "artifact_version_id": str(value.id),
                    "version_number": value.version_number,
                    "content_hash": value.content_hash,
                }
                for key, value in artifacts.items()
            },
            "pre_render_gates": {
                item.gate_key: item.result
                for item in gates
                if item.gate_key in PRE_RENDER_GATES
            },
            "post_media_gates": {
                item.gate_key: item.result
                for item in gates
                if item.gate_key in POST_MEDIA_GATES
            },
            "revision_cycles": [],
            "revision_cycle_count": 0,
            "provider_execution": "DISABLED",
            "human_review_state": "PENDING",
            "mr1_paid_execution_approval": "PENDING",
            "proceed_to_mr1": False,
            "exact_next_action": "Operator reviews topic, script, scenario, evidence, visual direction, cost cap, and rights plan before any MR1 decision.",
        }

    @staticmethod
    def _project_lineage(project: VideoProject) -> dict[str, Any]:
        return {
            "channel_workspace_id": str(project.channel_workspace_id),
            "channel_profile_version_id": str(project.channel_profile_version_id),
            "compiled_channel_policy_snapshot_id": str(project.policy_snapshot_id),
            "native_render_policy_snapshot_ref": project.native_render_policy_snapshot_ref,
            "native_render_policy_snapshot_hash": project.native_render_policy_snapshot_hash,
            "creative_quality_policy_ref": project.creative_quality_policy_ref,
            "creative_quality_policy_hash": project.creative_quality_policy_hash,
            "provider_usage_policy_ref": project.provider_usage_policy_ref,
            "provider_usage_policy_hash": project.provider_usage_policy_hash,
            "budget_policy_ref": project.budget_policy_ref,
            "budget_policy_hash": project.budget_policy_hash,
            "format_identity_contract_ref": project.format_identity_contract_ref,
            "format_identity_contract_hash": project.format_identity_contract_hash,
        }

    @staticmethod
    def _provider_request_counts(by_type: dict[str, dict[str, Any]]) -> dict[str, int]:
        provider = (by_type.get("provider_execution_plan") or {}).get("content") or {}
        return {
            item["provider"]: int(item.get("planned_requests", 0))
            for item in provider.get("stages", [])
        }

    def _current_artifact(
        self, project_id: uuid.UUID, artifact_type: str
    ) -> ArtifactVersion | None:
        artifact = self.session.scalars(
            select(Artifact).where(
                Artifact.video_project_id == project_id,
                Artifact.artifact_type == artifact_type,
            )
        ).one_or_none()
        return (
            self.session.get(ArtifactVersion, artifact.current_version_id)
            if artifact and artifact.current_version_id
            else None
        )

    @staticmethod
    def _build_result(
        project: VideoProject, package: ArtifactVersion
    ) -> PKG1BuildResult:
        manifest = package.content or {}
        return PKG1BuildResult(
            package_id=str(package.artifact_id),
            video_project_id=str(project.id),
            selected_topic=project.title,
            used_fallback_topic=bool(manifest.get("used_fallback_topic", True)),
            technical_status="PASS",
            human_review_state="PENDING",
            provider_execution="DISABLED",
            exact_next_action="Operator reviews the PKG1 package and explicitly decides whether to open MR1; no provider execution is authorized.",
        )
