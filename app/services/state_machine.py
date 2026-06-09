from sqlalchemy.orm import Session

from app.core.enums import ProjectState, ReviewActionType
from app.models.entities import VideoProject, VideoState


ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    ProjectState.IDEA_FOUND.value: {ProjectState.RESEARCHED.value, ProjectState.TOPIC_REVIEW.value},
    ProjectState.RESEARCHED.value: {ProjectState.TOPIC_REVIEW.value},
    ProjectState.TOPIC_REVIEW.value: {ProjectState.TOPIC_APPROVED.value, ProjectState.TOPIC_REJECTED.value},
    ProjectState.TOPIC_APPROVED.value: {ProjectState.COST_AND_URGENCY_CHECK.value},
    ProjectState.COST_AND_URGENCY_CHECK.value: {
        ProjectState.PRE_PRODUCTION_PACK_CREATED.value,
        ProjectState.WAITING_HUMAN_PRE_APPROVAL.value,
    },
    ProjectState.WAITING_HUMAN_PRE_APPROVAL.value: {
        ProjectState.PRE_APPROVED.value,
        ProjectState.PRE_APPROVAL_REJECTED.value,
        ProjectState.PRE_APPROVAL_EXPIRED.value,
    },
    ProjectState.PRE_APPROVED.value: {ProjectState.SCRIPT_DRAFTED.value},
    ProjectState.PRE_PRODUCTION_PACK_CREATED.value: {ProjectState.SCRIPT_DRAFTED.value},
    ProjectState.SCRIPT_DRAFTED.value: {ProjectState.SCRIPT_CRITIQUED.value},
    ProjectState.SCRIPT_CRITIQUED.value: {ProjectState.SCRIPT_REVIEW.value},
    ProjectState.SCRIPT_REVIEW.value: {
        ProjectState.SCRIPT_APPROVED.value,
        ProjectState.SCRIPT_REVISION_REQUESTED.value,
        ProjectState.SCRIPT_REJECTED.value,
    },
    ProjectState.SCRIPT_APPROVED.value: {ProjectState.STORYBOARD_CREATED.value},
    ProjectState.STORYBOARD_CREATED.value: {ProjectState.PROMPTS_CREATED.value},
    ProjectState.PROMPTS_CREATED.value: {ProjectState.ASSET_CONTEXT_CREATED.value},
    ProjectState.ASSET_CONTEXT_CREATED.value: {ProjectState.REUSABLE_ASSET_SEARCHED.value},
    ProjectState.REUSABLE_ASSET_SEARCHED.value: {ProjectState.ASSET_REUSED.value, ProjectState.ASSET_GENERATED.value},
    ProjectState.ASSET_REUSED.value: {ProjectState.MEDIA_GENERATED.value},
    ProjectState.ASSET_GENERATED.value: {ProjectState.MEDIA_GENERATED.value},
    ProjectState.MEDIA_GENERATED.value: {ProjectState.VOICE_GENERATED.value},
    ProjectState.VOICE_GENERATED.value: {ProjectState.SUBTITLE_CREATED.value},
    ProjectState.SUBTITLE_CREATED.value: {ProjectState.LOWFI_RENDERED.value},
    ProjectState.LOWFI_RENDERED.value: {ProjectState.LOWFI_QA_PASSED.value},
    ProjectState.LOWFI_QA_PASSED.value: {ProjectState.RENDER_TIMELINE_SAVED.value},
    ProjectState.RENDER_TIMELINE_SAVED.value: {ProjectState.MEDIA_QA.value},
    ProjectState.MEDIA_QA.value: {ProjectState.COMPLIANCE_CHECKED.value},
    ProjectState.COMPLIANCE_CHECKED.value: {ProjectState.AUTHORITY_FINAL_REVIEW.value},
    ProjectState.AUTHORITY_FINAL_REVIEW.value: {ProjectState.SEO_METADATA_CREATED.value},
    ProjectState.SEO_METADATA_CREATED.value: {ProjectState.REVIEW_TASK_CREATED.value},
    ProjectState.REVIEW_TASK_CREATED.value: {ProjectState.WAITING_HUMAN_REVIEW.value},
    ProjectState.WAITING_HUMAN_REVIEW.value: {
        ProjectState.HUMAN_APPROVED.value,
        ProjectState.HUMAN_REQUESTED_CHANGES.value,
        ProjectState.HUMAN_REJECTED.value,
        ProjectState.REVIEW_SLA_WARNING.value,
    },
    ProjectState.HUMAN_APPROVED.value: {ProjectState.PRE_PUBLISH_REVERIFY.value},
    ProjectState.HUMAN_REQUESTED_CHANGES.value: {ProjectState.SCRIPT_REVISION_REQUESTED.value},
    ProjectState.HUMAN_REJECTED.value: {ProjectState.SALVAGE_REQUIRED.value},
    ProjectState.PRE_PUBLISH_REVERIFY.value: {ProjectState.PUBLISHED.value, ProjectState.SALVAGE_REQUIRED.value},
    ProjectState.SALVAGE_REQUIRED.value: {
        ProjectState.SALVAGE_PLAN_CREATED.value,
        ProjectState.PRE_PUBLISH_REVERIFY.value,
        ProjectState.CONVERTING_TO_EVERGREEN.value,
        ProjectState.REPURPOSING_ASSETS.value,
        ProjectState.ARCHIVED_FOR_REUSE.value,
        ProjectState.HUMAN_REJECTED.value,
    },
    ProjectState.SALVAGE_PLAN_CREATED.value: {
        ProjectState.PUBLISHED.value,
        ProjectState.CONVERTING_TO_EVERGREEN.value,
        ProjectState.REPURPOSING_ASSETS.value,
        ProjectState.ARCHIVED_FOR_REUSE.value,
    },
    ProjectState.PUBLISHED.value: {ProjectState.PUBLISH_DETECTED.value},
    ProjectState.PUBLISH_DETECTED.value: {ProjectState.ANALYTICS_2H_COLLECTED.value},
    ProjectState.ANALYTICS_2H_COLLECTED.value: {ProjectState.ANALYTICS_4H_COLLECTED.value},
    ProjectState.ANALYTICS_4H_COLLECTED.value: {ProjectState.ANALYTICS_24H_COLLECTED.value},
    ProjectState.ANALYTICS_24H_COLLECTED.value: {ProjectState.ANALYTICS_48H_COLLECTED.value},
    ProjectState.ANALYTICS_48H_COLLECTED.value: {ProjectState.ANALYTICS_7D_COLLECTED.value},
    ProjectState.ANALYTICS_7D_COLLECTED.value: {ProjectState.AUTHORITY_POST_REVIEW.value},
    ProjectState.AUTHORITY_POST_REVIEW.value: {
        ProjectState.PLAYBOOK_NO_CHANGE.value,
        ProjectState.PLAYBOOK_MINOR_TUNING.value,
        ProjectState.PLAYBOOK_CORRECTIVE_ACTION.value,
    },
    ProjectState.PLAYBOOK_NO_CHANGE.value: {ProjectState.MEMORY_UPDATED.value},
    ProjectState.PLAYBOOK_MINOR_TUNING.value: {ProjectState.MEMORY_UPDATED.value},
    ProjectState.PLAYBOOK_CORRECTIVE_ACTION.value: {ProjectState.MEMORY_UPDATED.value},
}


class StateMachineService:
    def transition(self, db: Session, project: VideoProject, next_state: str, event: dict | None = None) -> VideoState:
        current = project.current_state
        allowed = ALLOWED_TRANSITIONS.get(current, set())
        if next_state != current and next_state not in allowed:
            raise ValueError(f"invalid transition {current} -> {next_state}")
        project.current_state = next_state
        row = VideoState(
            company_id=project.company_id,
            workspace_id=project.workspace_id,
            project_id=project.id,
            state=next_state,
            event_json=event or {},
        )
        db.add(row)
        return row

    def apply_review_action(self, db: Session, project: VideoProject, action: str) -> VideoState:
        if action in {ReviewActionType.APPROVE.value, ReviewActionType.PRE_APPROVE.value}:
            return self.transition(db, project, ProjectState.HUMAN_APPROVED.value, {"action": action})
        if action == ReviewActionType.REQUEST_CHANGES.value:
            return self.transition(db, project, ProjectState.HUMAN_REQUESTED_CHANGES.value, {"action": action})
        if action == ReviewActionType.REJECT.value:
            return self.transition(db, project, ProjectState.HUMAN_REJECTED.value, {"action": action})
        if action == ReviewActionType.CONVERT_TO_EVERGREEN.value:
            return self.transition(db, project, ProjectState.CONVERTING_TO_EVERGREEN.value, {"action": action})
        if action == ReviewActionType.REPURPOSE_ASSET.value:
            return self.transition(db, project, ProjectState.REPURPOSING_ASSETS.value, {"action": action})
        if action == ReviewActionType.ARCHIVE_FOR_REUSE.value:
            return self.transition(db, project, ProjectState.ARCHIVED_FOR_REUSE.value, {"action": action})
        if action == ReviewActionType.PUBLISH_ANYWAY.value:
            return self.transition(db, project, ProjectState.PUBLISHED.value, {"action": action})
        return self.transition(db, project, project.current_state, {"action": action})
