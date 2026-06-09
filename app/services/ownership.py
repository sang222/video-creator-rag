from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import ChannelWorkspace, ReviewTask, UploadedVideo, VideoProject


def assert_workspace_belongs_to_company(db: Session, company_id: str, workspace_id: str) -> ChannelWorkspace:
    workspace = db.execute(
        select(ChannelWorkspace).where(
            ChannelWorkspace.id == workspace_id,
            ChannelWorkspace.company_id == company_id,
        )
    ).scalar_one_or_none()
    if not workspace:
        raise HTTPException(status_code=404, detail="workspace not found for company")
    return workspace


def assert_project_belongs_to_workspace(
    db: Session,
    project_id: str,
    company_id: str,
    workspace_id: str,
) -> VideoProject:
    project = db.execute(
        select(VideoProject).where(
            VideoProject.id == project_id,
            VideoProject.company_id == company_id,
            VideoProject.workspace_id == workspace_id,
        )
    ).scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=400, detail="project does not belong to company/workspace")
    return project


def assert_uploaded_video_belongs_to_workspace(
    db: Session,
    uploaded_video_id: str,
    company_id: str,
    workspace_id: str,
) -> UploadedVideo:
    video = db.execute(
        select(UploadedVideo).where(
            UploadedVideo.id == uploaded_video_id,
            UploadedVideo.company_id == company_id,
            UploadedVideo.workspace_id == workspace_id,
        )
    ).scalar_one_or_none()
    if not video:
        raise HTTPException(status_code=400, detail="uploaded video does not belong to company/workspace")
    return video


def assert_review_task_belongs_to_project_scope(
    db: Session,
    task_id: str,
    *,
    project_id: str | None = None,
    company_id: str | None = None,
    workspace_id: str | None = None,
) -> ReviewTask:
    task = db.get(ReviewTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="review task not found")
    if project_id is not None and task.project_id != project_id:
        raise HTTPException(status_code=400, detail="review task does not belong to project")
    if company_id is not None and task.company_id != company_id:
        raise HTTPException(status_code=400, detail="review task does not belong to company")
    if workspace_id is not None and task.workspace_id != workspace_id:
        raise HTTPException(status_code=400, detail="review task does not belong to workspace")
    if task.project_id:
        assert_project_belongs_to_workspace(db, task.project_id, task.company_id, task.workspace_id)
    return task
