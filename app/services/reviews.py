from sqlalchemy.orm import Session

from app.core.enums import ProjectState, ReviewActionType, ReviewTaskStatus, ReviewTaskType, WorkflowMode
from app.models.entities import ReviewAction, ReviewTask, VideoProject
from app.services.state_machine import StateMachineService


class ReviewTaskService:
    def create_task(
        self,
        db: Session,
        *,
        company_id: str,
        workspace_id: str,
        project_id: str | None,
        task_type: str,
        title: str,
        payload_json: dict,
    ) -> ReviewTask:
        task = ReviewTask(
            company_id=company_id,
            workspace_id=workspace_id,
            project_id=project_id,
            task_type=task_type,
            title=title,
            payload_json=payload_json,
            status=ReviewTaskStatus.OPEN.value,
        )
        db.add(task)
        return task

    def apply_action(
        self,
        db: Session,
        *,
        task: ReviewTask,
        action: str,
        actor: str | None,
        notes: str | None,
        payload_json: dict,
    ) -> ReviewAction:
        if task.status != ReviewTaskStatus.OPEN.value:
            raise ValueError("review task is not open")
        if not task.project_id:
            raise ValueError("review task must belong to a project before action can be applied")
        project = db.get(VideoProject, task.project_id)
        if not project:
            raise ValueError("review task project was not found")
        if project.company_id != task.company_id or project.workspace_id != task.workspace_id:
            raise ValueError("review task project scope mismatch")
        self._validate_and_transition_project(db, task, project, action, payload_json)

        row = ReviewAction(
            company_id=task.company_id,
            workspace_id=task.workspace_id,
            project_id=task.project_id,
            review_task_id=task.id,
            action=action,
            actor=actor,
            notes=notes,
            payload_json=payload_json,
        )
        if action in {
            ReviewActionType.APPROVE.value,
            ReviewActionType.PRE_APPROVE.value,
            ReviewActionType.PUBLISH_ANYWAY.value,
            ReviewActionType.DOWNGRADE_MODE.value,
            ReviewActionType.CONVERT_TO_EVERGREEN.value,
            ReviewActionType.REPURPOSE_ASSET.value,
            ReviewActionType.ARCHIVE_FOR_REUSE.value,
        }:
            task.status = ReviewTaskStatus.APPROVED.value
        elif action == ReviewActionType.REQUEST_CHANGES.value:
            task.status = ReviewTaskStatus.CHANGES_REQUESTED.value
        elif action == ReviewActionType.REJECT.value:
            task.status = ReviewTaskStatus.REJECTED.value
        else:
            raise ValueError(f"unsupported review action: {action}")
        db.add(row)
        return row

    def _validate_and_transition_project(
        self,
        db: Session,
        task: ReviewTask,
        project: VideoProject,
        action: str,
        payload_json: dict,
    ) -> None:
        state_machine = StateMachineService()

        if task.task_type == ReviewTaskType.PRE_SPEND.value:
            if project.current_state != ProjectState.WAITING_HUMAN_PRE_APPROVAL.value:
                raise ValueError("PRE_SPEND action requires project state WAITING_HUMAN_PRE_APPROVAL")
            if action == ReviewActionType.PRE_APPROVE.value:
                state_machine.transition(db, project, ProjectState.PRE_APPROVED.value, {"action": action})
                return
            if action == ReviewActionType.DOWNGRADE_MODE.value:
                project.workflow_mode = payload_json.get("workflow_mode") or WorkflowMode.NORMAL_REUSE_FIRST.value
                state_machine.transition(
                    db,
                    project,
                    ProjectState.PRE_APPROVED.value,
                    {"action": action, "workflow_mode": project.workflow_mode},
                )
                return
            if action == ReviewActionType.REJECT.value:
                state_machine.transition(db, project, ProjectState.PRE_APPROVAL_REJECTED.value, {"action": action})
                return
            raise ValueError(f"invalid action {action} for PRE_SPEND review task")

        if task.task_type == ReviewTaskType.FINAL_VIDEO.value:
            if project.current_state != ProjectState.WAITING_HUMAN_REVIEW.value:
                raise ValueError("FINAL_VIDEO action requires project state WAITING_HUMAN_REVIEW")
            if action == ReviewActionType.APPROVE.value:
                state_machine.transition(db, project, ProjectState.HUMAN_APPROVED.value, {"action": action})
                return
            if action == ReviewActionType.REQUEST_CHANGES.value:
                state_machine.transition(db, project, ProjectState.HUMAN_REQUESTED_CHANGES.value, {"action": action})
                return
            if action == ReviewActionType.REJECT.value:
                state_machine.transition(db, project, ProjectState.HUMAN_REJECTED.value, {"action": action})
                return
            raise ValueError(f"invalid action {action} for FINAL_VIDEO review task")

        if task.task_type == ReviewTaskType.SALVAGE.value:
            if project.current_state != ProjectState.SALVAGE_REQUIRED.value:
                raise ValueError("SALVAGE action requires project state SALVAGE_REQUIRED")
            if action == ReviewActionType.PUBLISH_ANYWAY.value:
                state_machine.transition(db, project, ProjectState.PRE_PUBLISH_REVERIFY.value, {"action": action})
                return
            if action == ReviewActionType.CONVERT_TO_EVERGREEN.value:
                state_machine.transition(db, project, ProjectState.CONVERTING_TO_EVERGREEN.value, {"action": action})
                return
            if action == ReviewActionType.REPURPOSE_ASSET.value:
                state_machine.transition(db, project, ProjectState.REPURPOSING_ASSETS.value, {"action": action})
                return
            if action == ReviewActionType.ARCHIVE_FOR_REUSE.value:
                state_machine.transition(db, project, ProjectState.ARCHIVED_FOR_REUSE.value, {"action": action})
                return
            if action == ReviewActionType.REJECT.value:
                state_machine.transition(db, project, ProjectState.HUMAN_REJECTED.value, {"action": action})
                return
            raise ValueError(f"invalid action {action} for SALVAGE review task")

        raise ValueError(f"unsupported review task type: {task.task_type}")
