import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.contracts import AuditEnvelope, EventEnvelope
from app.contracts.workflow import (
    ApprovalDecisionCreate,
    ArtifactCreate,
    ArtifactVersionCreate,
    ReviewFindingCreate,
    ReviewTaskCreate,
    RevisionRequestCreate,
    VideoProjectCreate,
)
from app.core.errors import ConflictError, ForbiddenError, NotFoundError, ValidationFailureError
from app.core.time import utc_now
from app.db.models import (
    ApprovalDecision,
    Artifact,
    ArtifactVersion,
    AuditEvent,
    ChannelWorkspace,
    CompiledChannelPolicySnapshot,
    DomainEvent,
    ReviewFinding,
    ReviewTask,
    RevisionRequest,
    User,
    VideoProject,
)
from app.services.audit import AuditService
from app.services.config_registry import ConfigRegistryService, content_hash
from app.services.domain_events import DomainEventBus
from app.services.rbac import RBACService

ARTIFACT_TYPE_REGISTRY = Path("config/artifact_type_registry.yaml")
REVIEW_TYPE_REGISTRY = Path("config/review_type_registry.yaml")
DECISION_RIGHTS_POLICY = Path("config/decision_rights_policy.yaml")

FORBIDDEN_ALLOWANCE_KEYS = {
    "raw_vendor_payload",
    "vendor_payload",
    "prompt",
    "prompts",
    "trace",
    "traces",
    "waveform",
    "blob",
    "policy_prose",
    "free_form_policy_prose",
}


class DecisionRightsService:
    DEFAULT_ACTION_PERMISSIONS = {
        "video_project.create": "workflow:project:create",
        "artifact.create": "workflow:artifact:create",
        "artifact_version.create": "workflow:artifact_version:create",
        "review_task.create": "workflow:review:create",
        "review_finding.create": "workflow:review:perform",
        "revision_request.create": "workflow:revision:create",
        "revision_request.resolve": "workflow:revision:resolve",
        "approval_decision.create": "workflow:approval:decide",
        "workflow.read": "workflow:read",
    }

    def __init__(self, session: Session):
        self.session = session

    def required_permission(self, action: str) -> str:
        try:
            loaded = ConfigRegistryService(self.session).validate_catalog(DECISION_RIGHTS_POLICY)
        except FileNotFoundError:
            return self.DEFAULT_ACTION_PERMISSIONS[action]
        for item in loaded.content["items"]:
            if item["key"] == action:
                return item["required_permission"]
        if action not in self.DEFAULT_ACTION_PERMISSIONS:
            raise ValidationFailureError(f"unknown workflow action: {action}")
        return self.DEFAULT_ACTION_PERMISSIONS[action]

    def has_capability(
        self,
        *,
        user_id: uuid.UUID,
        company_id: uuid.UUID,
        action: str,
    ) -> bool:
        permission = self.required_permission(action)
        return RBACService(self.session).user_has_permission(
            user_id=user_id,
            permission=permission,
            company_id=company_id,
        )

    def require_capability(
        self,
        *,
        user_id: uuid.UUID,
        company_id: uuid.UUID,
        action: str,
    ) -> None:
        if not self.has_capability(user_id=user_id, company_id=company_id, action=action):
            raise ForbiddenError(f"missing permission for {action}")


class VideoProjectService:
    def __init__(self, session: Session):
        self.session = session

    def create_project(
        self,
        *,
        data: VideoProjectCreate,
        correlation_id: str = "m2-project-create",
    ) -> VideoProject:
        channel = self.session.get(ChannelWorkspace, data.channel_workspace_id)
        if channel is None:
            raise NotFoundError(f"channel not found: {data.channel_workspace_id}")
        if channel.company_id != data.company_id:
            raise ValidationFailureError("channel does not belong to project company")
        snapshot = self.session.get(CompiledChannelPolicySnapshot, data.policy_snapshot_id)
        if snapshot is None:
            raise NotFoundError(f"policy snapshot not found: {data.policy_snapshot_id}")
        if snapshot.channel_workspace_id != data.channel_workspace_id:
            raise ValidationFailureError("policy snapshot does not belong to project channel")
        if snapshot.status != "active" or channel.active_policy_snapshot_id != snapshot.id:
            raise ValidationFailureError("policy snapshot must be active for project creation")
        _require_user(self.session, data.created_by_user_id, "created_by_user_id")
        if data.owner_user_id is not None:
            _require_user(self.session, data.owner_user_id, "owner_user_id")
        DecisionRightsService(self.session).require_capability(
            user_id=data.created_by_user_id,
            company_id=data.company_id,
            action="video_project.create",
        )
        project = VideoProject(**data.model_dump())
        self.session.add(project)
        self.session.flush()
        _record_workflow_event(
            self.session,
            event_type="video_project.created",
            aggregate_type="video_project",
            aggregate_id=project.id,
            actor_id=data.created_by_user_id,
            target_type="video_project",
            target_id=project.id,
            company_id=project.company_id,
            correlation_id=correlation_id,
            payload={
                "channel_workspace_id": str(project.channel_workspace_id),
                "policy_snapshot_id": str(project.policy_snapshot_id),
            },
        )
        return project

    def get_project(self, project_id: uuid.UUID) -> VideoProject | None:
        return self.session.get(VideoProject, project_id)

    def inspect_workflow_state(self, project_id: uuid.UUID) -> dict[str, Any]:
        project = self.get_project(project_id)
        if project is None:
            raise NotFoundError(f"project not found: {project_id}")
        artifacts = list(
            self.session.scalars(
                select(Artifact).where(Artifact.video_project_id == project_id).order_by(Artifact.created_at.asc())
            ).all()
        )
        artifact_ids = [artifact.id for artifact in artifacts]
        versions = list(
            self.session.scalars(
                select(ArtifactVersion)
                .where(ArtifactVersion.artifact_id.in_(artifact_ids))
                .order_by(ArtifactVersion.created_at.asc())
            ).all()
        ) if artifact_ids else []
        reviews = list(
            self.session.scalars(
                select(ReviewTask).where(ReviewTask.video_project_id == project_id).order_by(ReviewTask.created_at.asc())
            ).all()
        )
        review_ids = [review.id for review in reviews]
        revisions = list(
            self.session.scalars(
                select(RevisionRequest)
                .where(RevisionRequest.review_task_id.in_(review_ids))
                .order_by(RevisionRequest.created_at.asc())
            ).all()
        ) if review_ids else []
        version_ids = [version.id for version in versions]
        approvals = list(
            self.session.scalars(
                select(ApprovalDecision)
                .where(ApprovalDecision.target_artifact_version_id.in_(version_ids))
                .order_by(ApprovalDecision.created_at.asc())
            ).all()
        ) if version_ids else []
        current_by_version = {artifact.current_version_id: artifact.id for artifact in artifacts if artifact.current_version_id}
        version_to_artifact = {version.id: version.artifact_id for version in versions}
        return {
            "project_id": str(project.id),
            "policy_snapshot_id": str(project.policy_snapshot_id),
            "artifacts": [
                {
                    "id": str(artifact.id),
                    "artifact_type": artifact.artifact_type,
                    "current_version_id": str(artifact.current_version_id) if artifact.current_version_id else None,
                }
                for artifact in artifacts
            ],
            "artifact_versions": [
                {
                    "id": str(version.id),
                    "artifact_id": str(version.artifact_id),
                    "version_number": version.version_number,
                    "content_hash": version.content_hash,
                }
                for version in versions
            ],
            "review_tasks": [
                {
                    "id": str(review.id),
                    "target_type": review.target_type,
                    "target_id": str(review.target_id),
                    "target_artifact_version_id": str(review.target_artifact_version_id) if review.target_artifact_version_id else None,
                    "status": review.status,
                }
                for review in reviews
            ],
            "revision_requests": [
                {
                    "id": str(revision.id),
                    "target_artifact_version_id": str(revision.target_artifact_version_id),
                    "resolved_by_artifact_version_id": str(revision.resolved_by_artifact_version_id) if revision.resolved_by_artifact_version_id else None,
                    "status": revision.status,
                }
                for revision in revisions
            ],
            "approval_decisions": [
                {
                    "id": str(approval.id),
                    "target_artifact_version_id": str(approval.target_artifact_version_id) if approval.target_artifact_version_id else None,
                    "decision": approval.decision,
                    "stale_for_current_artifact_version": (
                        approval.target_artifact_version_id not in current_by_version
                        and version_to_artifact.get(approval.target_artifact_version_id) is not None
                    ),
                }
                for approval in approvals
            ],
        }


class ArtifactService:
    def __init__(self, session: Session):
        self.session = session

    def create_artifact(
        self,
        *,
        data: ArtifactCreate,
        correlation_id: str = "m2-artifact-create",
    ) -> Artifact:
        project = self.session.get(VideoProject, data.video_project_id)
        if project is None:
            raise NotFoundError(f"project not found: {data.video_project_id}")
        _require_user(self.session, data.created_by_user_id, "created_by_user_id")
        _require_registry_key(self.session, ARTIFACT_TYPE_REGISTRY, data.artifact_type, "artifact_type")
        DecisionRightsService(self.session).require_capability(
            user_id=data.created_by_user_id,
            company_id=project.company_id,
            action="artifact.create",
        )
        artifact = Artifact(**data.model_dump())
        self.session.add(artifact)
        self.session.flush()
        _record_workflow_event(
            self.session,
            event_type="artifact.created",
            aggregate_type="artifact",
            aggregate_id=artifact.id,
            actor_id=data.created_by_user_id,
            target_type="artifact",
            target_id=artifact.id,
            company_id=project.company_id,
            correlation_id=correlation_id,
            payload={"video_project_id": str(project.id), "artifact_type": artifact.artifact_type},
        )
        return artifact

    def create_artifact_version(
        self,
        *,
        data: ArtifactVersionCreate,
        set_current: bool = True,
        correlation_id: str = "m2-artifact-version-create",
    ) -> ArtifactVersion:
        artifact = self.session.get(Artifact, data.artifact_id)
        if artifact is None:
            raise NotFoundError(f"artifact not found: {data.artifact_id}")
        project = self.session.get(VideoProject, artifact.video_project_id)
        if project is None:
            raise NotFoundError(f"project not found: {artifact.video_project_id}")
        _require_user(self.session, data.created_by_user_id, "created_by_user_id")
        DecisionRightsService(self.session).require_capability(
            user_id=data.created_by_user_id,
            company_id=project.company_id,
            action="artifact_version.create",
        )
        max_version = self.session.scalar(
            select(func.max(ArtifactVersion.version_number)).where(ArtifactVersion.artifact_id == artifact.id)
        ) or 0
        if max_version == 0 and data.parent_version_id is not None:
            raise ValidationFailureError("v1 cannot have a parent version")
        if max_version > 0 and data.parent_version_id is None:
            raise ValidationFailureError("parent_version_id is required after v1")
        if data.parent_version_id is not None:
            parent = self.session.get(ArtifactVersion, data.parent_version_id)
            if parent is None:
                raise NotFoundError(f"parent artifact version not found: {data.parent_version_id}")
            if parent.artifact_id != artifact.id:
                raise ValidationFailureError("parent version must belong to the same artifact")
        _validate_allowance_payloads(data)
        payload = data.model_dump()
        payload["version_number"] = max_version + 1
        payload["content_hash"] = deterministic_artifact_content_hash(data.content)
        version = ArtifactVersion(**payload)
        self.session.add(version)
        self.session.flush()
        if set_current:
            artifact.current_version_id = version.id
            self.session.flush()
        _record_workflow_event(
            self.session,
            event_type="artifact_version.created",
            aggregate_type="artifact_version",
            aggregate_id=version.id,
            actor_id=data.created_by_user_id,
            target_type="artifact_version",
            target_id=version.id,
            company_id=project.company_id,
            correlation_id=correlation_id,
            payload={
                "artifact_id": str(artifact.id),
                "video_project_id": str(project.id),
                "version_number": version.version_number,
                "content_hash": version.content_hash,
            },
        )
        return version

    def get_artifact(self, artifact_id: uuid.UUID) -> Artifact | None:
        return self.session.get(Artifact, artifact_id)

    def get_artifact_version(self, artifact_version_id: uuid.UUID) -> ArtifactVersion | None:
        return self.session.get(ArtifactVersion, artifact_version_id)


class ReviewService:
    def __init__(self, session: Session):
        self.session = session

    def create_review_task(
        self,
        *,
        data: ReviewTaskCreate,
        correlation_id: str = "m2-review-task-create",
    ) -> ReviewTask:
        project = self.session.get(VideoProject, data.video_project_id)
        if project is None:
            raise NotFoundError(f"project not found: {data.video_project_id}")
        _require_user(self.session, data.requested_by_user_id, "requested_by_user_id")
        if data.assigned_to_user_id is not None:
            _require_user(self.session, data.assigned_to_user_id, "assigned_to_user_id")
        _require_registry_key(self.session, REVIEW_TYPE_REGISTRY, data.review_type, "review_type")
        self._validate_exact_review_target(project, data)
        DecisionRightsService(self.session).require_capability(
            user_id=data.requested_by_user_id,
            company_id=project.company_id,
            action="review_task.create",
        )
        review_task = ReviewTask(**data.model_dump())
        self.session.add(review_task)
        self.session.flush()
        _record_workflow_event(
            self.session,
            event_type="review_task.created",
            aggregate_type="review_task",
            aggregate_id=review_task.id,
            actor_id=data.requested_by_user_id,
            target_type="review_task",
            target_id=review_task.id,
            company_id=project.company_id,
            correlation_id=correlation_id,
            payload={
                "video_project_id": str(project.id),
                "review_type": review_task.review_type,
                "target_type": review_task.target_type,
                "target_id": str(review_task.target_id),
                "target_artifact_version_id": str(review_task.target_artifact_version_id) if review_task.target_artifact_version_id else None,
            },
        )
        return review_task

    def add_finding(
        self,
        *,
        data: ReviewFindingCreate,
        correlation_id: str = "m2-review-finding-create",
    ) -> ReviewFinding:
        review_task = self.session.get(ReviewTask, data.review_task_id)
        if review_task is None:
            raise NotFoundError(f"review task not found: {data.review_task_id}")
        project = self.session.get(VideoProject, review_task.video_project_id)
        if project is None:
            raise NotFoundError(f"project not found: {review_task.video_project_id}")
        _require_user(self.session, data.created_by_user_id, "created_by_user_id")
        DecisionRightsService(self.session).require_capability(
            user_id=data.created_by_user_id,
            company_id=project.company_id,
            action="review_finding.create",
        )
        finding = ReviewFinding(**data.model_dump())
        self.session.add(finding)
        self.session.flush()
        _record_workflow_event(
            self.session,
            event_type="review_finding.created",
            aggregate_type="review_finding",
            aggregate_id=finding.id,
            actor_id=data.created_by_user_id,
            target_type="review_finding",
            target_id=finding.id,
            company_id=project.company_id,
            correlation_id=correlation_id,
            payload={"review_task_id": str(review_task.id), "severity": finding.severity},
        )
        return finding

    def create_revision_request(
        self,
        *,
        data: RevisionRequestCreate,
        correlation_id: str = "m2-revision-request-create",
    ) -> RevisionRequest:
        review_task = self.session.get(ReviewTask, data.review_task_id)
        if review_task is None:
            raise NotFoundError(f"review task not found: {data.review_task_id}")
        project = self.session.get(VideoProject, review_task.video_project_id)
        if project is None:
            raise NotFoundError(f"project not found: {review_task.video_project_id}")
        target_version = self.session.get(ArtifactVersion, data.target_artifact_version_id)
        if target_version is None:
            raise NotFoundError(f"artifact version not found: {data.target_artifact_version_id}")
        if review_task.target_artifact_version_id != target_version.id:
            raise ValidationFailureError("revision request must target the review task artifact version")
        _require_user(self.session, data.requested_by_user_id, "requested_by_user_id")
        DecisionRightsService(self.session).require_capability(
            user_id=data.requested_by_user_id,
            company_id=project.company_id,
            action="revision_request.create",
        )
        revision = RevisionRequest(**data.model_dump())
        self.session.add(revision)
        self.session.flush()
        _record_workflow_event(
            self.session,
            event_type="revision_request.created",
            aggregate_type="revision_request",
            aggregate_id=revision.id,
            actor_id=data.requested_by_user_id,
            target_type="revision_request",
            target_id=revision.id,
            company_id=project.company_id,
            correlation_id=correlation_id,
            payload={
                "review_task_id": str(review_task.id),
                "target_artifact_version_id": str(target_version.id),
            },
        )
        return revision

    def resolve_revision_request(
        self,
        *,
        revision_request_id: uuid.UUID,
        resolved_by_artifact_version_id: uuid.UUID,
        actor_user_id: uuid.UUID | None = None,
        correlation_id: str = "m2-revision-request-resolve",
    ) -> RevisionRequest:
        revision = self.session.get(RevisionRequest, revision_request_id)
        if revision is None:
            raise NotFoundError(f"revision request not found: {revision_request_id}")
        old_version = self.session.get(ArtifactVersion, revision.target_artifact_version_id)
        new_version = self.session.get(ArtifactVersion, resolved_by_artifact_version_id)
        if old_version is None or new_version is None:
            raise NotFoundError("artifact version not found for revision resolution")
        if old_version.id == new_version.id:
            raise ValidationFailureError("revision resolution requires a new artifact version")
        if old_version.artifact_id != new_version.artifact_id:
            raise ValidationFailureError("resolved version must belong to the same artifact")
        if new_version.version_number <= old_version.version_number:
            raise ValidationFailureError("resolved version must be newer than the target version")
        review_task = self.session.get(ReviewTask, revision.review_task_id)
        project = self.session.get(VideoProject, review_task.video_project_id) if review_task else None
        if project is None:
            raise NotFoundError("project not found for revision request")
        actor_id = actor_user_id or new_version.created_by_user_id
        DecisionRightsService(self.session).require_capability(
            user_id=actor_id,
            company_id=project.company_id,
            action="revision_request.resolve",
        )
        revision.status = "resolved"
        revision.resolved_by_artifact_version_id = new_version.id
        revision.resolved_at = utc_now()
        self.session.flush()
        _record_workflow_event(
            self.session,
            event_type="revision_request.resolved",
            aggregate_type="revision_request",
            aggregate_id=revision.id,
            actor_id=actor_id,
            target_type="revision_request",
            target_id=revision.id,
            company_id=project.company_id,
            correlation_id=correlation_id,
            payload={
                "target_artifact_version_id": str(old_version.id),
                "resolved_by_artifact_version_id": str(new_version.id),
            },
        )
        return revision

    def _validate_exact_review_target(self, project: VideoProject, data: ReviewTaskCreate) -> None:
        if data.target_type == "artifact_version":
            if data.target_artifact_version_id is None:
                raise ValidationFailureError("artifact version review requires target_artifact_version_id")
            if data.target_id != data.target_artifact_version_id:
                raise ValidationFailureError("artifact version review target_id must equal target_artifact_version_id")
            version = self.session.get(ArtifactVersion, data.target_artifact_version_id)
            if version is None:
                raise NotFoundError(f"artifact version not found: {data.target_artifact_version_id}")
            artifact = self.session.get(Artifact, version.artifact_id)
            if artifact is None or artifact.video_project_id != project.id:
                raise ValidationFailureError("artifact version does not belong to review project")
            return
        if data.target_type == "video_project":
            if data.target_id != project.id:
                raise ValidationFailureError("video_project review target_id must equal video_project_id")
            if data.target_artifact_version_id is not None:
                raise ValidationFailureError("video_project review cannot carry artifact version target")
            return
        raise ValidationFailureError(f"unsupported review target_type: {data.target_type}")


class ApprovalService:
    def __init__(self, session: Session):
        self.session = session

    def create_approval_decision(
        self,
        *,
        data: ApprovalDecisionCreate,
        correlation_id: str = "m2-approval-decision-create",
    ) -> ApprovalDecision:
        project, version = self._validate_exact_approval_target(data)
        _require_user(self.session, data.decided_by_user_id, "decided_by_user_id")
        if data.decision == "approved" and version is not None and version.created_by_user_id == data.decided_by_user_id:
            _record_workflow_event(
                self.session,
                event_type="approval_decision.rejected_or_blocked",
                aggregate_type="approval_decision",
                aggregate_id=data.target_id,
                actor_id=data.decided_by_user_id,
                target_type=data.target_type,
                target_id=data.target_id,
                company_id=project.company_id,
                correlation_id=correlation_id,
                payload={"reason": "SELF_APPROVAL_BLOCKED", "target_artifact_version_id": str(version.id)},
            )
            raise ForbiddenError("creator cannot self-approve own artifact version")
        try:
            DecisionRightsService(self.session).require_capability(
                user_id=data.decided_by_user_id,
                company_id=project.company_id,
                action="approval_decision.create",
            )
        except ForbiddenError:
            _record_workflow_event(
                self.session,
                event_type="approval_decision.rejected_or_blocked",
                aggregate_type="approval_decision",
                aggregate_id=data.target_id,
                actor_id=data.decided_by_user_id,
                target_type=data.target_type,
                target_id=data.target_id,
                company_id=project.company_id,
                correlation_id=correlation_id,
                payload={"reason": "DECISION_RIGHTS_DENIED"},
            )
            raise
        payload = data.model_dump()
        metadata = payload.pop("metadata")
        decision = ApprovalDecision(
            **payload,
            metadata_=metadata,
            decided_at=utc_now(),
        )
        self.session.add(decision)
        self.session.flush()
        _record_workflow_event(
            self.session,
            event_type="approval_decision.created",
            aggregate_type="approval_decision",
            aggregate_id=decision.id,
            actor_id=data.decided_by_user_id,
            target_type="approval_decision",
            target_id=decision.id,
            company_id=project.company_id,
            correlation_id=correlation_id,
            payload={
                "decision": decision.decision,
                "target_type": decision.target_type,
                "target_id": str(decision.target_id),
                "target_artifact_version_id": str(decision.target_artifact_version_id) if decision.target_artifact_version_id else None,
            },
        )
        if data.decision in {"rejected", "blocked"}:
            _record_workflow_event(
                self.session,
                event_type="approval_decision.rejected_or_blocked",
                aggregate_type="approval_decision",
                aggregate_id=decision.id,
                actor_id=data.decided_by_user_id,
                target_type="approval_decision",
                target_id=decision.id,
                company_id=project.company_id,
                correlation_id=correlation_id,
                payload={"decision": decision.decision},
            )
        return decision

    def is_decision_stale_for_current_version(self, decision_id: uuid.UUID) -> bool:
        decision = self.session.get(ApprovalDecision, decision_id)
        if decision is None:
            raise NotFoundError(f"approval decision not found: {decision_id}")
        if decision.target_artifact_version_id is None:
            return False
        version = self.session.get(ArtifactVersion, decision.target_artifact_version_id)
        if version is None:
            raise NotFoundError("approval target artifact version not found")
        artifact = self.session.get(Artifact, version.artifact_id)
        if artifact is None:
            raise NotFoundError("approval target artifact not found")
        return artifact.current_version_id != version.id

    def _validate_exact_approval_target(
        self,
        data: ApprovalDecisionCreate,
    ) -> tuple[VideoProject, ArtifactVersion | None]:
        if data.target_type == "artifact_version":
            if data.target_artifact_version_id is None:
                raise ValidationFailureError("artifact version approval requires target_artifact_version_id")
            if data.target_id != data.target_artifact_version_id:
                raise ValidationFailureError("artifact version approval target_id must equal target_artifact_version_id")
            version = self.session.get(ArtifactVersion, data.target_artifact_version_id)
            if version is None:
                raise NotFoundError(f"artifact version not found: {data.target_artifact_version_id}")
            artifact = self.session.get(Artifact, version.artifact_id)
            if artifact is None:
                raise NotFoundError(f"artifact not found: {version.artifact_id}")
            project = self.session.get(VideoProject, artifact.video_project_id)
            if project is None:
                raise NotFoundError(f"project not found: {artifact.video_project_id}")
            return project, version
        if data.target_type == "review_task":
            review_task = self.session.get(ReviewTask, data.target_id)
            if review_task is None:
                raise NotFoundError(f"review task not found: {data.target_id}")
            if review_task.target_artifact_version_id != data.target_artifact_version_id:
                raise ValidationFailureError("approval must carry the exact review task artifact version")
            project = self.session.get(VideoProject, review_task.video_project_id)
            if project is None:
                raise NotFoundError(f"project not found: {review_task.video_project_id}")
            version = self.session.get(ArtifactVersion, data.target_artifact_version_id) if data.target_artifact_version_id else None
            return project, version
        if data.target_type == "video_project":
            project = self.session.get(VideoProject, data.target_id)
            if project is None:
                raise NotFoundError(f"project not found: {data.target_id}")
            if data.target_artifact_version_id is not None:
                raise ValidationFailureError("video_project approval cannot carry artifact version target")
            return project, None
        raise ValidationFailureError(f"unsupported approval target_type: {data.target_type}")


def deterministic_artifact_content_hash(content: dict[str, Any]) -> str:
    return content_hash(content)


def _require_user(session: Session, user_id: uuid.UUID, field_name: str) -> None:
    if session.get(User, user_id) is None:
        raise NotFoundError(f"{field_name} user not found: {user_id}")


def _require_registry_key(session: Session, path: Path, key: str, label: str) -> None:
    loaded = ConfigRegistryService(session).validate_catalog(path)
    keys = {item["key"] for item in loaded.content["items"]}
    if key not in keys:
        raise ValidationFailureError(f"unknown {label}: {key}")


def _validate_allowance_payloads(data: ArtifactVersionCreate) -> None:
    payloads: list[Any] = [
        data.external_entity_refs,
        data.packaging_metadata,
        data.media_qc_metadata,
        data.source_manifest,
        data.evidence_refs,
        data.context_refs,
        data.claim_refs,
    ]
    for payload in payloads:
        for key in _walk_keys(payload):
            normalized = key.lower().replace("-", "_")
            if normalized in FORBIDDEN_ALLOWANCE_KEYS:
                raise ValidationFailureError(f"forbidden allowance payload key: {key}")


def _walk_keys(value: Any) -> list[str]:
    if isinstance(value, dict):
        keys = list(value.keys())
        for child in value.values():
            keys.extend(_walk_keys(child))
        return keys
    if isinstance(value, list):
        keys: list[str] = []
        for item in value:
            keys.extend(_walk_keys(item))
        return keys
    return []


def _record_workflow_event(
    session: Session,
    *,
    event_type: str,
    aggregate_type: str,
    aggregate_id: uuid.UUID,
    actor_id: uuid.UUID | None,
    target_type: str,
    target_id: uuid.UUID,
    company_id: uuid.UUID,
    correlation_id: str,
    payload: dict[str, Any],
) -> tuple[AuditEvent, DomainEvent]:
    audit = AuditService(session).append(
        AuditEnvelope(
            actor_type="user" if actor_id else "system",
            actor_id=actor_id,
            action=event_type,
            target_type=target_type,
            target_id=target_id,
            reason_code="AUDIT_EVENT_RECORDED",
            correlation_id=correlation_id,
            payload=payload,
        ),
        company_id=company_id,
    )
    domain = DomainEventBus(session).append(
        EventEnvelope(
            event_type=event_type,
            event_version=1,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            payload=payload,
            metadata={"actor_id": str(actor_id) if actor_id else None},
            correlation_id=correlation_id,
            causation_id=audit.id,
        ),
        company_id=company_id,
    )
    return audit, domain
