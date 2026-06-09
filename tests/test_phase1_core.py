import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.core.enums import MemoryScope, ProjectState, ReviewActionType, ReviewTaskType
from app.db.base import Base
from app.memory.router import MemoryRouter
from app.models.entities import (
    ChannelWorkspace,
    Company,
    ComplianceReport,
    CostEvent,
    MemoryItem,
    QAReport,
    RenderTimeline,
    ReviewTask,
    VideoProject,
)
from app.services.maturity import WorkspaceMaturityService
from app.services.reviews import ReviewTaskService
from app.services.state_machine import StateMachineService
from app.services.workflow_mode import WorkflowModeRouter
from app.schemas.api import AuthorityDecision
from app.workflows.phase1 import Phase1WorkflowService


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = Session()
    try:
        yield session
    finally:
        session.close()


def create_company_workspace(db, *, followers=120, videos=3, confidence=0.05, name="AI Education Daily"):
    company = Company(name="Acme Media")
    db.add(company)
    db.flush()
    workspace = ChannelWorkspace(
        company_id=company.id,
        workspace_name=name,
        platform="youtube",
        channel_name=name,
        niche="AI creator tools",
        language="en",
        target_market=["US", "UK", "CA"],
        follower_count=followers,
        published_video_count=videos,
        baseline_confidence=confidence,
    )
    stage, _ = WorkspaceMaturityService().classify_workspace(workspace)
    workspace.maturity_stage = stage
    db.add(workspace)
    db.commit()
    return company, workspace


@pytest.mark.parametrize(
    ("followers", "videos", "confidence", "expected"),
    [
        (120, 3, 0.05, "NEW_CHANNEL"),
        (2_000, 12, 0.4, "EXPLORING_CHANNEL"),
        (50_000, 40, 0.7, "GROWING_CHANNEL"),
        (150_000, 45, 0.8, "SCALED_CHANNEL"),
        (600_000, 240, 0.9, "MATURE_BRAND_CHANNEL"),
    ],
)
def test_maturity_classifier_covers_all_stages(db, followers, videos, confidence, expected):
    _, workspace = create_company_workspace(db, followers=followers, videos=videos, confidence=confidence)
    stage, _ = WorkspaceMaturityService().classify_workspace(workspace)
    assert stage == expected


def test_workflow_mode_router_prioritizes_monetization_validation_for_new_channel():
    context = {
        "maturity_stage": "NEW_CHANNEL",
        "baseline_confidence": 0.05,
        "budget": {"cost_per_video_target": 1.0, "hard_max_per_video": 9.0},
    }
    result = WorkflowModeRouter().route(context)
    assert result.selected_mode == "MONETIZATION_VALIDATION_MODE"
    assert result.media_policy["max_new_ai_video_scenes"] == 0
    assert result.budget["hard_max"] == 2.5


def test_memory_search_is_workspace_scoped(db):
    company, workspace_a = create_company_workspace(db, name="Workspace A")
    workspace_b = ChannelWorkspace(
        company_id=company.id,
        workspace_name="Workspace B",
        platform="youtube",
        channel_name="Workspace B",
        niche="finance",
        follower_count=50,
        published_video_count=1,
        baseline_confidence=0.01,
        maturity_stage="NEW_CHANNEL",
    )
    db.add(workspace_b)
    db.flush()
    item_a = MemoryItem(
        company_id=company.id,
        workspace_id=workspace_a.id,
        scope=MemoryScope.WORKSPACE_ONLY.value,
        family="monetization_memory",
        type="note",
        title="AI tool affiliate path",
        content="Buyer intent for AI creator software is strong.",
    )
    item_b = MemoryItem(
        company_id=company.id,
        workspace_id=workspace_b.id,
        scope=MemoryScope.WORKSPACE_ONLY.value,
        family="monetization_memory",
        type="note",
        title="Finance card affiliate path",
        content="Credit card buyer intent belongs only to workspace B.",
    )
    db.add_all([item_a, item_b])
    db.commit()

    router = MemoryRouter()
    router.embed_item(db, item_a)
    router.embed_item(db, item_b)
    results = router.retrieve_memory(
        db,
        agent_role="AuthorityAgent",
        workspace_context={"company_id": company.id, "workspace_id": workspace_a.id},
        query="credit card affiliate",
        families=["monetization_memory"],
        scopes=[MemoryScope.WORKSPACE_ONLY.value],
        limit=10,
    )
    assert [item.workspace_id for item in results] == [workspace_a.id]
    assert item_b.id not in {item.id for item in results}


def test_state_machine_rejects_invalid_transition(db):
    company, workspace = create_company_workspace(db)
    project = VideoProject(company_id=company.id, workspace_id=workspace.id, title="Test", current_state="IDEA_FOUND")
    db.add(project)
    db.commit()
    with pytest.raises(ValueError):
        StateMachineService().transition(db, project, ProjectState.PUBLISHED.value, {})


def test_review_action_updates_waiting_project_state(db):
    company, workspace = create_company_workspace(db)
    project = VideoProject(
        company_id=company.id,
        workspace_id=workspace.id,
        title="Test",
        current_state=ProjectState.WAITING_HUMAN_REVIEW.value,
    )
    db.add(project)
    db.flush()
    task = ReviewTaskService().create_task(
        db,
        company_id=company.id,
        workspace_id=workspace.id,
        project_id=project.id,
        task_type=ReviewTaskType.FINAL_VIDEO.value,
        title="Final review",
        payload_json={},
    )
    db.flush()
    action = ReviewTaskService().apply_action(
        db,
        task=task,
        action=ReviewActionType.APPROVE.value,
        actor="tester",
        notes=None,
        payload_json={},
    )
    db.commit()
    assert action.action == "APPROVE"
    assert task.status == "APPROVED"
    assert project.current_state == ProjectState.HUMAN_APPROVED.value


def test_pre_spend_pre_approve_transitions_to_pre_approved(db):
    company, workspace = create_company_workspace(db)
    project = VideoProject(
        company_id=company.id,
        workspace_id=workspace.id,
        title="Pre spend",
        current_state=ProjectState.WAITING_HUMAN_PRE_APPROVAL.value,
        workflow_mode="PREMIUM_HYBRID",
    )
    db.add(project)
    db.flush()
    task = ReviewTaskService().create_task(
        db,
        company_id=company.id,
        workspace_id=workspace.id,
        project_id=project.id,
        task_type=ReviewTaskType.PRE_SPEND.value,
        title="Pre-spend",
        payload_json={},
    )
    db.flush()
    ReviewTaskService().apply_action(
        db,
        task=task,
        action=ReviewActionType.PRE_APPROVE.value,
        actor="tester",
        notes=None,
        payload_json={},
    )
    assert project.current_state == ProjectState.PRE_APPROVED.value


def test_salvage_convert_to_evergreen_transitions(db):
    company, workspace = create_company_workspace(db)
    project = VideoProject(
        company_id=company.id,
        workspace_id=workspace.id,
        title="Salvage",
        current_state=ProjectState.SALVAGE_REQUIRED.value,
    )
    db.add(project)
    db.flush()
    task = ReviewTaskService().create_task(
        db,
        company_id=company.id,
        workspace_id=workspace.id,
        project_id=project.id,
        task_type=ReviewTaskType.SALVAGE.value,
        title="Salvage",
        payload_json={},
    )
    db.flush()
    ReviewTaskService().apply_action(
        db,
        task=task,
        action=ReviewActionType.CONVERT_TO_EVERGREEN.value,
        actor="tester",
        notes=None,
        payload_json={},
    )
    assert project.current_state == ProjectState.CONVERTING_TO_EVERGREEN.value


def test_workflow_requiring_preapproval_pauses_at_pre_spend(db):
    company, workspace = create_company_workspace(db, followers=150_000, videos=80, confidence=0.8)
    project = VideoProject(company_id=company.id, workspace_id=workspace.id, title="Premium", current_state="IDEA_FOUND")
    db.add(project)
    db.commit()

    task = Phase1WorkflowService().run_to_review_task(db, project)

    assert task.task_type == ReviewTaskType.PRE_SPEND.value
    assert project.current_state == ProjectState.WAITING_HUMAN_PRE_APPROVAL.value
    assert db.scalar(select(RenderTimeline).where(RenderTimeline.project_id == project.id)) is None


def test_mock_workflow_reaches_review_task_and_records_artifacts_costs(db):
    company, workspace = create_company_workspace(db)
    project = VideoProject(company_id=company.id, workspace_id=workspace.id, title="Mock project", current_state="IDEA_FOUND")
    db.add(project)
    db.commit()

    task = Phase1WorkflowService().run_to_review_task(db, project)

    assert task.task_type == ReviewTaskType.FINAL_VIDEO.value
    assert project.current_state == ProjectState.WAITING_HUMAN_REVIEW.value
    assert db.scalar(select(RenderTimeline).where(RenderTimeline.project_id == project.id)) is not None
    assert db.scalar(select(QAReport).where(QAReport.project_id == project.id)) is not None
    assert db.scalar(select(ComplianceReport).where(ComplianceReport.project_id == project.id)) is not None
    assert db.scalar(select(CostEvent).where(CostEvent.project_id == project.id)) is not None


def test_authority_decision_enum_rejects_publish():
    with pytest.raises(ValidationError):
        AuthorityDecision(
            decision="PUBLISH",
            monetization_passability_impact="POSITIVE",
            revenue_impact="MEDIUM",
            policy_risk="LOW",
            brand_fit_score=8.0,
            audience_fit_score=8.0,
            buyer_intent_score=8.0,
        )
