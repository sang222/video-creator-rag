from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.enums import MemoryScope, ProjectState, ReviewTaskType
from app.db.session import get_db, init_db
from app.memory.router import MemoryRouter, validate_memory_scope_payload
from app.models.entities import (
    AnalyticsSnapshot,
    AgentRun,
    AssetLibrary,
    ChannelWorkspace,
    Company,
    CostEvent,
    EditorialPlaybook,
    MemoryItem,
    QAReport,
    RenderTimeline,
    ReviewTask,
    UploadedVideo,
    VideoArtifact,
    VideoProject,
    VideoState,
    WorkspaceBudgetPolicy,
    WorkspaceProfile,
)
from app.schemas.api import (
    AnalyticsSnapshotCreate,
    AnalyticsSnapshotOut,
    CompanyCreate,
    CompanyOut,
    ConstitutionOut,
    CostReportOut,
    MarkPublishedRequest,
    MemoryBulkCreate,
    MemoryContextPackRequest,
    MemoryItemCreate,
    MemoryItemOut,
    MemorySearchRequest,
    ProjectArtifactsOut,
    ProjectOut,
    ProjectStart,
    ProjectStateOut,
    ReviewTaskCardOut,
    ReviewActionOut,
    ReviewActionRequest,
    ReviewTaskCreate,
    ReviewTaskOut,
    UploadedVideoCreate,
    UploadedVideoOut,
    WorkspaceCreate,
    WorkspaceOut,
)
from app.services.context import ContextCompilerService, WorkspaceContextService
from app.services.maturity import WorkspaceMaturityService
from app.services.ownership import (
    assert_project_belongs_to_workspace,
    assert_uploaded_video_belongs_to_workspace,
    assert_workspace_belongs_to_company,
)
from app.services.reviews import ReviewTaskService
from app.services.skill_pack import SkillPackLoader
from app.workflows.phase1 import Phase1WorkflowService
from app.config.settings import get_settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    if get_settings().validate_skill_packs_on_startup:
        SkillPackLoader().assert_required()
    yield


app = FastAPI(title="Company-level Editorial Content Operating System - Phase 2", lifespan=lifespan)


def get_or_404(db: Session, model, id_: str):
    row = db.get(model, id_)
    if not row:
        raise HTTPException(status_code=404, detail=f"{model.__name__} not found")
    return row


def assert_project_scope_query(
    db: Session,
    project: VideoProject,
    *,
    company_id: str | None = None,
    workspace_id: str | None = None,
) -> None:
    if company_id and project.company_id != company_id:
        raise HTTPException(status_code=404, detail="project not found for company")
    if workspace_id and project.workspace_id != workspace_id:
        raise HTTPException(status_code=404, detail="project not found for workspace")
    if company_id and workspace_id:
        assert_workspace_belongs_to_company(db, company_id, workspace_id)


def required_actions_for_task(task_type: str) -> list[str]:
    return {
        "PRE_SPEND": ["PRE_APPROVE", "DOWNGRADE_MODE", "REJECT"],
        "FINAL_VIDEO": ["APPROVE", "REQUEST_CHANGES", "REJECT"],
        "SALVAGE": ["PUBLISH_ANYWAY", "CONVERT_TO_EVERGREEN", "REPURPOSE_ASSET", "ARCHIVE_FOR_REUSE", "REJECT"],
    }.get(task_type, [])


def build_review_task_card(db: Session, task: ReviewTask) -> dict:
    project = db.get(VideoProject, task.project_id) if task.project_id else None
    linked_artifact_ids = []
    linked_agent_run_ids = []
    if project:
        linked_artifact_ids = list(db.scalars(select(VideoArtifact.id).where(VideoArtifact.project_id == project.id)))
        linked_agent_run_ids = list(db.scalars(select(AgentRun.id).where(AgentRun.project_id == project.id)))
    return {
        "id": task.id,
        "company_id": task.company_id,
        "workspace_id": task.workspace_id,
        "project_id": task.project_id,
        "task_type": task.task_type,
        "status": task.status,
        "title": task.title,
        "summary": task.payload_json.get("summary") if isinstance(task.payload_json, dict) else task.title,
        "payload_json": task.payload_json,
        "due_at": task.due_at,
        "created_at": task.created_at,
        "updated_at": task.updated_at,
        "project_state": project.current_state if project else None,
        "required_actions": required_actions_for_task(task.task_type),
        "linked_artifact_ids": linked_artifact_ids,
        "linked_agent_run_ids": linked_agent_run_ids,
    }


@app.post("/companies", response_model=CompanyOut)
def create_company(payload: CompanyCreate, db: Session = Depends(get_db)):
    company = Company(name=payload.name, default_language=payload.default_language, config_json=payload.config_json)
    db.add(company)
    db.commit()
    db.refresh(company)
    return company


@app.get("/companies", response_model=list[CompanyOut])
def list_companies(db: Session = Depends(get_db)):
    return list(db.scalars(select(Company)))


@app.post("/workspaces", response_model=WorkspaceOut)
def create_workspace(payload: WorkspaceCreate, db: Session = Depends(get_db)):
    get_or_404(db, Company, payload.company_id)
    workspace = ChannelWorkspace(
        company_id=payload.company_id,
        workspace_name=payload.workspace_name,
        platform=payload.platform,
        platform_channel_id=payload.platform_channel_id,
        channel_name=payload.channel_name,
        channel_url=payload.channel_url,
        niche=payload.niche,
        language=payload.language,
        target_market=payload.target_market,
        follower_count=payload.follower_count,
        published_video_count=payload.published_video_count,
        monetization_status=payload.monetization_status,
        baseline_confidence=payload.baseline_confidence,
    )
    stage, _ = WorkspaceMaturityService().classify_workspace(workspace)
    workspace.maturity_stage = stage
    db.add(workspace)
    db.flush()

    profile_payload = payload.profile
    profile = WorkspaceProfile(
        workspace_id=workspace.id,
        company_id=workspace.company_id,
        brand_voice=profile_payload.brand_voice if profile_payload else "clear, practical, not hype",
        target_audience=profile_payload.target_audience if profile_payload else None,
        forbidden_topics=profile_payload.forbidden_topics if profile_payload else [],
        preferred_formats=profile_payload.preferred_formats if profile_payload else [],
        target_market=profile_payload.target_market if profile_payload else payload.target_market,
        monetization_thesis_json=profile_payload.monetization_thesis_json if profile_payload else {},
        platform_rules_json=profile_payload.platform_rules_json if profile_payload else {},
        human_review_required=profile_payload.human_review_required if profile_payload else True,
        default_workflow_mode=profile_payload.default_workflow_mode if profile_payload else "MONETIZATION_VALIDATION_MODE",
        config_json=profile_payload.config_json if profile_payload else {},
    )
    db.add(profile)
    db.add(
        WorkspaceBudgetPolicy(
            company_id=workspace.company_id,
            workspace_id=workspace.id,
            cost_per_video_target=1.0,
            hard_max_per_video=2.5,
            daily_budget_limit=5.0,
        )
    )
    db.add(
        EditorialPlaybook(
            company_id=workspace.company_id,
            workspace_id=workspace.id,
            version="seed_playbook_v1",
            content_json={"principles": ["validate revenue path before scaling", "reuse first", "avoid AI spam"]},
            active=True,
        )
    )
    db.commit()
    db.refresh(workspace)
    return workspace


@app.get("/workspaces", response_model=list[WorkspaceOut])
def list_workspaces(company_id: str | None = Query(default=None), db: Session = Depends(get_db)):
    if not company_id:
        raise HTTPException(status_code=400, detail="company_id filter is required")
    get_or_404(db, Company, company_id)
    return list(db.scalars(select(ChannelWorkspace).where(ChannelWorkspace.company_id == company_id)))


@app.get("/workspaces/{id}", response_model=WorkspaceOut)
def get_workspace(id: str, db: Session = Depends(get_db)):
    return get_or_404(db, ChannelWorkspace, id)


@app.post("/workspaces/{id}/classify-maturity")
def classify_workspace(id: str, db: Session = Depends(get_db)):
    workspace = get_or_404(db, ChannelWorkspace, id)
    snapshot = WorkspaceMaturityService().classify_and_persist(db, workspace)
    return {"workspace_id": id, "maturity_stage": snapshot.maturity_stage, "reason": snapshot.reason_json}


@app.get("/workspaces/{id}/context")
def get_workspace_context(id: str, db: Session = Depends(get_db)):
    context = WorkspaceContextService().get_context(db, id)
    constitution = ContextCompilerService().get_active_or_compile(db, id)
    return {"workspace_context": context, "compiled_workspace_operational_constitution": constitution.content}


@app.post("/workspaces/{workspace_id}/compile-constitution", response_model=ConstitutionOut)
def compile_workspace_constitution(workspace_id: str, db: Session = Depends(get_db)):
    get_or_404(db, ChannelWorkspace, workspace_id)
    return ContextCompilerService().compile(db, workspace_id)


@app.get("/workspaces/{workspace_id}/constitution", response_model=ConstitutionOut)
def get_workspace_constitution(workspace_id: str, db: Session = Depends(get_db)):
    get_or_404(db, ChannelWorkspace, workspace_id)
    return ContextCompilerService().get_active_or_compile(db, workspace_id)


@app.post("/workspaces/{workspace_id}/uploaded-videos", response_model=UploadedVideoOut)
def create_uploaded_video(workspace_id: str, payload: UploadedVideoCreate, db: Session = Depends(get_db)):
    workspace = get_or_404(db, ChannelWorkspace, workspace_id)
    if payload.project_id:
        assert_project_belongs_to_workspace(db, payload.project_id, workspace.company_id, workspace.id)
    video = UploadedVideo(company_id=workspace.company_id, workspace_id=workspace.id, **payload.model_dump())
    db.add(video)
    db.commit()
    db.refresh(video)
    return video


@app.get("/workspaces/{workspace_id}/uploaded-videos", response_model=list[UploadedVideoOut])
def list_uploaded_videos(workspace_id: str, db: Session = Depends(get_db)):
    workspace = get_or_404(db, ChannelWorkspace, workspace_id)
    return list(
        db.scalars(
            select(UploadedVideo).where(
                UploadedVideo.company_id == workspace.company_id,
                UploadedVideo.workspace_id == workspace.id,
            )
        )
    )


@app.get("/uploaded-videos", response_model=list[UploadedVideoOut])
def list_uploaded_videos_scoped(
    company_id: str | None = Query(default=None),
    workspace_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    if not company_id and not workspace_id:
        raise HTTPException(status_code=400, detail="company_id or workspace_id filter is required")
    stmt = select(UploadedVideo)
    if company_id:
        stmt = stmt.where(UploadedVideo.company_id == company_id)
    if workspace_id:
        if company_id:
            assert_workspace_belongs_to_company(db, company_id, workspace_id)
        stmt = stmt.where(UploadedVideo.workspace_id == workspace_id)
    return list(db.scalars(stmt))


@app.get("/uploaded-videos/{id}", response_model=UploadedVideoOut)
def get_uploaded_video(id: str, db: Session = Depends(get_db)):
    return get_or_404(db, UploadedVideo, id)


@app.post("/wakeup/company")
def wakeup_company(company_id: str, db: Session = Depends(get_db)):
    company = get_or_404(db, Company, company_id)
    workspaces = list(db.scalars(select(ChannelWorkspace).where(ChannelWorkspace.company_id == company.id)))
    return {"state": ProjectState.COMPANY_WAKEUP.value, "company_id": company.id, "workspace_count": len(workspaces)}


@app.post("/wakeup/workspace/{workspace_id}")
def wakeup_workspace(workspace_id: str, db: Session = Depends(get_db)):
    context = WorkspaceContextService().get_context(db, workspace_id)
    return {"state": ProjectState.WORKSPACES_LOADED.value, "workspace_context": context}


@app.post("/projects/start", response_model=ProjectOut)
def start_project(payload: ProjectStart, db: Session = Depends(get_db)):
    get_or_404(db, Company, payload.company_id)
    assert_workspace_belongs_to_company(db, payload.company_id, payload.workspace_id)
    project = VideoProject(
        company_id=payload.company_id,
        workspace_id=payload.workspace_id,
        title=payload.title,
        topic=payload.topic,
        current_state=ProjectState.IDEA_FOUND.value,
        metadata_json={},
    )
    db.add(project)
    db.flush()
    db.add(
        VideoState(
            company_id=project.company_id,
            workspace_id=project.workspace_id,
            project_id=project.id,
            state=project.current_state,
            event_json={"event": "project_started"},
        )
    )
    db.commit()
    db.refresh(project)
    return project


@app.get("/projects", response_model=list[ProjectOut])
def list_projects(
    company_id: str | None = Query(default=None),
    workspace_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    if not company_id and not workspace_id:
        raise HTTPException(status_code=400, detail="company_id or workspace_id filter is required")
    stmt = select(VideoProject)
    if company_id:
        stmt = stmt.where(VideoProject.company_id == company_id)
    if workspace_id:
        if company_id:
            assert_workspace_belongs_to_company(db, company_id, workspace_id)
        stmt = stmt.where(VideoProject.workspace_id == workspace_id)
    return list(db.scalars(stmt))


@app.get("/projects/{id}", response_model=ProjectOut)
def get_project(
    id: str,
    company_id: str | None = Query(default=None),
    workspace_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    project = get_or_404(db, VideoProject, id)
    assert_project_scope_query(db, project, company_id=company_id, workspace_id=workspace_id)
    return project


@app.get("/projects/{id}/state", response_model=ProjectStateOut)
def get_project_state(id: str, db: Session = Depends(get_db)):
    project = get_or_404(db, VideoProject, id)
    history = [
        {"state": row.state, "event": row.event_json, "created_at": row.created_at.isoformat()}
        for row in db.scalars(select(VideoState).where(VideoState.project_id == id).order_by(VideoState.created_at))
    ]
    return {"project_id": project.id, "current_state": project.current_state, "history": history}


@app.post("/projects/{id}/run-next")
def run_project_next(id: str, db: Session = Depends(get_db)):
    project = get_or_404(db, VideoProject, id)
    if project.current_state in {ProjectState.IDEA_FOUND.value, ProjectState.PRE_APPROVED.value}:
        task = Phase1WorkflowService().run_to_review_task(db, project)
        return {"project_id": project.id, "current_state": project.current_state, "review_task_id": task.id}
    if project.current_state == ProjectState.ANALYTICS_7D_COLLECTED.value:
        result = Phase1WorkflowService().run_post_publish_diagnosis(db, project)
        return {"project_id": project.id, "current_state": project.current_state, "result": result}
    return {"project_id": project.id, "current_state": project.current_state, "message": "No automatic step for current state."}


@app.get("/projects/{id}/review-pack")
def get_review_pack(id: str, db: Session = Depends(get_db)):
    get_or_404(db, VideoProject, id)
    task = db.execute(
        select(ReviewTask).where(ReviewTask.project_id == id).order_by(ReviewTask.created_at.desc())
    ).scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="review pack not found")
    return task.payload_json


@app.post("/projects/{id}/review-tasks", response_model=ReviewTaskOut)
def create_review_task(id: str, payload: ReviewTaskCreate, db: Session = Depends(get_db)):
    project = get_or_404(db, VideoProject, id)
    assert_project_belongs_to_workspace(db, project.id, project.company_id, project.workspace_id)
    task = ReviewTaskService().create_task(
        db,
        company_id=project.company_id,
        workspace_id=project.workspace_id,
        project_id=project.id,
        task_type=payload.task_type.value if hasattr(payload.task_type, "value") else str(payload.task_type),
        title=payload.title,
        payload_json=payload.payload_json,
    )
    db.commit()
    db.refresh(task)
    return task


@app.get("/review-tasks", response_model=list[ReviewTaskOut])
def list_review_tasks(
    company_id: str | None = Query(default=None),
    workspace_id: str | None = Query(default=None),
    project_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    if not any([company_id, workspace_id, project_id]):
        raise HTTPException(status_code=400, detail="company_id, workspace_id, or project_id filter is required")
    stmt = select(ReviewTask)
    if project_id:
        project = get_or_404(db, VideoProject, project_id)
        if company_id and project.company_id != company_id:
            raise HTTPException(status_code=400, detail="project does not belong to company")
        if workspace_id and project.workspace_id != workspace_id:
            raise HTTPException(status_code=400, detail="project does not belong to workspace")
        stmt = stmt.where(ReviewTask.project_id == project_id)
    if company_id:
        stmt = stmt.where(ReviewTask.company_id == company_id)
    if workspace_id:
        if company_id:
            assert_workspace_belongs_to_company(db, company_id, workspace_id)
        stmt = stmt.where(ReviewTask.workspace_id == workspace_id)
    if status:
        stmt = stmt.where(ReviewTask.status == status)
    return list(db.scalars(stmt))


@app.get("/review-tasks/{id}", response_model=ReviewTaskCardOut)
def get_review_task(
    id: str,
    company_id: str | None = Query(default=None),
    workspace_id: str | None = Query(default=None),
    project_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    task = get_or_404(db, ReviewTask, id)
    if company_id and task.company_id != company_id:
        raise HTTPException(status_code=404, detail="review task not found for company")
    if workspace_id and task.workspace_id != workspace_id:
        raise HTTPException(status_code=404, detail="review task not found for workspace")
    if project_id and task.project_id != project_id:
        raise HTTPException(status_code=404, detail="review task not found for project")
    if company_id and workspace_id:
        assert_workspace_belongs_to_company(db, company_id, workspace_id)
    return build_review_task_card(db, task)


@app.post("/review-tasks/{id}/action", response_model=ReviewActionOut)
def act_on_review_task(id: str, payload: ReviewActionRequest, db: Session = Depends(get_db)):
    task = get_or_404(db, ReviewTask, id)
    try:
        action = ReviewTaskService().apply_action(
            db,
            task=task,
            action=payload.action.value,
            actor=payload.actor,
            notes=payload.notes,
            payload_json=payload.payload_json,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    db.commit()
    db.refresh(action)
    return action


@app.post("/projects/{id}/salvage-decision")
def salvage_decision(id: str, db: Session = Depends(get_db)):
    from app.agents.mocks import SalvageStrategyAgent

    project = get_or_404(db, VideoProject, id)
    result = SalvageStrategyAgent()(
        db,
        company_id=project.company_id,
        workspace_id=project.workspace_id,
        project_id=project.id,
        payload={"current_state": project.current_state},
    )
    if project.current_state == ProjectState.SALVAGE_REQUIRED.value:
        from app.services.state_machine import StateMachineService

        StateMachineService().transition(db, project, ProjectState.SALVAGE_PLAN_CREATED.value, result)
    db.commit()
    return result


@app.get("/projects/{id}/cost-report", response_model=CostReportOut)
def get_cost_report(id: str, db: Session = Depends(get_db)):
    get_or_404(db, VideoProject, id)
    events = list(db.scalars(select(CostEvent).where(CostEvent.project_id == id)))
    cost_by_agent: dict[str, float] = {}
    cost_by_provider_model: dict[str, float] = {}
    for event in events:
        cost_by_agent[event.agent_name] = cost_by_agent.get(event.agent_name, 0.0) + event.estimated_cost
        provider_model = f"{event.provider}/{event.model or 'unknown'}"
        cost_by_provider_model[provider_model] = cost_by_provider_model.get(provider_model, 0.0) + event.estimated_cost
    return {
        "project_id": id,
        "total_cost": sum(event.estimated_cost for event in events),
        "total_input_tokens": sum(event.input_tokens for event in events),
        "total_output_tokens": sum(event.output_tokens for event in events),
        "total_media_units": sum(event.media_units for event in events),
        "cost_by_agent": cost_by_agent,
        "cost_by_provider_model": cost_by_provider_model,
        "events": [
            {
                "agent_name": event.agent_name,
                "node_name": event.node_name,
                "provider": event.provider,
                "model": event.model,
                "input_tokens": event.input_tokens,
                "output_tokens": event.output_tokens,
                "media_units": event.media_units,
                "estimated_cost": event.estimated_cost,
                "raw_usage_json": event.raw_usage_json,
                "created_at": event.created_at.isoformat(),
            }
            for event in events
        ],
    }


@app.get("/projects/{id}/artifacts", response_model=ProjectArtifactsOut)
def get_project_artifacts(
    id: str,
    company_id: str | None = Query(default=None),
    workspace_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    project = get_or_404(db, VideoProject, id)
    assert_project_scope_query(db, project, company_id=company_id, workspace_id=workspace_id)
    return {
        "project_id": id,
        "artifacts": [
            {
                "id": row.id,
                "project_id": row.project_id,
                "type": row.artifact_type,
                "name": (row.content_json or {}).get("name") or (row.content_json or {}).get("title") or row.artifact_type,
                "status": (row.content_json or {}).get("status", "CREATED"),
                "uri": row.uri,
                "metadata": row.content_json,
                "created_at": row.created_at,
            }
            for row in db.scalars(select(VideoArtifact).where(VideoArtifact.project_id == id))
        ],
        "render_timelines": [
            {"id": row.id, "version": row.version, "timeline_json": row.timeline_json, "manifest_json": row.manifest_json}
            for row in db.scalars(select(RenderTimeline).where(RenderTimeline.project_id == id))
        ],
        "qa_reports": [
            {"id": row.id, "qa_type": row.qa_type, "score": row.score, "report_json": row.report_json}
            for row in db.scalars(select(QAReport).where(QAReport.project_id == id))
        ],
    }


@app.post("/projects/{id}/mark-published", response_model=UploadedVideoOut)
def mark_published(id: str, payload: MarkPublishedRequest, db: Session = Depends(get_db)):
    project = get_or_404(db, VideoProject, id)
    workspace = get_or_404(db, ChannelWorkspace, project.workspace_id)
    video = UploadedVideo(
        company_id=project.company_id,
        workspace_id=project.workspace_id,
        project_id=project.id,
        platform=workspace.platform,
        platform_video_id=payload.platform_video_id,
        video_url=payload.video_url,
        title=payload.title or project.title,
        publish_time=payload.publish_time or datetime.now(timezone.utc),
        upload_status="PUBLISHED",
        monetization_status="UNKNOWN",
        metadata_json={"source": "mark_published_endpoint"},
    )
    db.add(video)
    db.flush()
    Phase1WorkflowService().mark_published(db, project, video)
    return video


@app.post("/projects/{id}/analytics/snapshot", response_model=AnalyticsSnapshotOut)
def record_analytics_snapshot(id: str, payload: AnalyticsSnapshotCreate, db: Session = Depends(get_db)):
    project = get_or_404(db, VideoProject, id)
    if payload.uploaded_video_id:
        assert_uploaded_video_belongs_to_workspace(db, payload.uploaded_video_id, project.company_id, project.workspace_id)
    snapshot_data = payload.model_dump()
    snapshot_data["snapshot_time"] = snapshot_data["snapshot_time"] or datetime.now(timezone.utc)
    snapshot = AnalyticsSnapshot(
        company_id=project.company_id,
        workspace_id=project.workspace_id,
        project_id=project.id,
        **snapshot_data,
    )
    db.add(snapshot)
    db.flush()
    Phase1WorkflowService().record_analytics_state(db, project, snapshot)
    db.commit()
    db.refresh(snapshot)
    return snapshot


@app.post("/memory/items", response_model=MemoryItemOut)
def create_memory_item(payload: MemoryItemCreate, db: Session = Depends(get_db)):
    get_or_404(db, Company, payload.company_id)
    try:
        validate_memory_scope_payload(
            company_id=payload.company_id,
            workspace_id=payload.workspace_id,
            scope=payload.scope,
            metadata_json=payload.metadata_json,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if payload.workspace_id:
        assert_workspace_belongs_to_company(db, payload.company_id, payload.workspace_id)
    item = MemoryItem(**payload.model_dump())
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


@app.post("/memory/items/bulk", response_model=list[MemoryItemOut])
def create_memory_items_bulk(payload: MemoryBulkCreate, db: Session = Depends(get_db)):
    items: list[MemoryItem] = []
    for item_payload in payload.items:
        get_or_404(db, Company, item_payload.company_id)
        try:
            validate_memory_scope_payload(
                company_id=item_payload.company_id,
                workspace_id=item_payload.workspace_id,
                scope=item_payload.scope,
                metadata_json=item_payload.metadata_json,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if item_payload.workspace_id:
            assert_workspace_belongs_to_company(db, item_payload.company_id, item_payload.workspace_id)
        item = MemoryItem(**item_payload.model_dump())
        db.add(item)
        items.append(item)
    db.commit()
    for item in items:
        db.refresh(item)
        if payload.embed:
            try:
                MemoryRouter().embed_item(db, item)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
    return items


@app.post("/memory/items/{id}/embed", response_model=MemoryItemOut)
def embed_memory_item(id: str, db: Session = Depends(get_db)):
    item = get_or_404(db, MemoryItem, id)
    try:
        return MemoryRouter().embed_item(db, item)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/memory/search", response_model=list[MemoryItemOut])
def search_memory(payload: MemorySearchRequest, db: Session = Depends(get_db)):
    workspace = assert_workspace_belongs_to_company(db, payload.company_id, payload.workspace_id)
    context = {
        "company_id": payload.company_id,
        "workspace_id": payload.workspace_id,
        "platform": workspace.platform,
    }
    try:
        return MemoryRouter().retrieve_memory(
            db,
            agent_role=payload.agent_role,
            workspace_context=context,
            query=payload.query,
            families=payload.families,
            scopes=payload.scopes,
            limit=payload.top_k or payload.limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/memory/context-pack")
def build_memory_context_pack(payload: MemoryContextPackRequest, db: Session = Depends(get_db)):
    workspace = assert_workspace_belongs_to_company(db, payload.company_id, payload.workspace_id)
    if payload.project_id:
        assert_project_belongs_to_workspace(db, payload.project_id, payload.company_id, payload.workspace_id)
    try:
        return MemoryRouter().build_context_pack(
            db,
            agent_role=payload.agent_role,
            workspace_id=payload.workspace_id,
            project_id=payload.project_id,
            query=payload.query,
            families=payload.families,
            scopes=payload.scopes,
            workspace_context={
                "company_id": payload.company_id,
                "workspace_id": payload.workspace_id,
                "platform": workspace.platform,
            },
            limit=payload.top_k or payload.limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
