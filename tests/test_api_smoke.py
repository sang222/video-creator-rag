from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.main import app, get_db


@pytest.fixture()
def client():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def override_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()


def test_http_smoke_flow_to_review_action(client):
    company = client.post("/companies", json={"name": "Acme Media"}).json()
    workspace = client.post(
        "/workspaces",
        json={
            "company_id": company["id"],
            "workspace_name": "AI Edu US",
            "platform": "youtube",
            "channel_name": "AI Education Daily",
            "niche": "AI creator tools",
            "target_market": ["US", "UK", "CA"],
        },
    ).json()
    context = client.get(f"/workspaces/{workspace['id']}/context").json()
    assert context["workspace_context"]["maturity_stage"] == "NEW_CHANNEL"
    assert "Rule #1" in context["compiled_workspace_operational_constitution"]

    project = client.post(
        "/projects/start",
        json={"company_id": company["id"], "workspace_id": workspace["id"], "title": "Mock video"},
    ).json()
    run = client.post(f"/projects/{project['id']}/run-next").json()
    assert run["current_state"] == "WAITING_HUMAN_REVIEW"

    action = client.post(f"/review-tasks/{run['review_task_id']}/action", json={"action": "APPROVE"}).json()
    assert action["action"] == "APPROVE"

    state = client.get(f"/projects/{project['id']}/state").json()
    assert state["current_state"] == "HUMAN_APPROVED"


def create_company_and_workspace(client, name):
    company = client.post("/companies", json={"name": name}).json()
    workspace = client.post(
        "/workspaces",
        json={
            "company_id": company["id"],
            "workspace_name": f"{name} Workspace",
            "platform": "youtube",
            "channel_name": f"{name} Channel",
            "niche": "AI creator tools",
            "target_market": ["US"],
        },
    ).json()
    return company, workspace


def test_cannot_create_project_with_workspace_from_other_company(client):
    company_a, _ = create_company_and_workspace(client, "A")
    _, workspace_b = create_company_and_workspace(client, "B")

    response = client.post(
        "/projects/start",
        json={"company_id": company_a["id"], "workspace_id": workspace_b["id"], "title": "Bad scope"},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "workspace not found for company"


def test_cannot_create_uploaded_video_with_project_from_other_workspace(client):
    company_a, workspace_a = create_company_and_workspace(client, "A")
    company_b, workspace_b = create_company_and_workspace(client, "B")
    project_b = client.post(
        "/projects/start",
        json={"company_id": company_b["id"], "workspace_id": workspace_b["id"], "title": "Other project"},
    ).json()

    response = client.post(
        f"/workspaces/{workspace_a['id']}/uploaded-videos",
        json={"platform": "youtube", "title": "Cross upload", "project_id": project_b["id"]},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "project does not belong to company/workspace"


def test_cannot_create_analytics_snapshot_with_uploaded_video_from_other_workspace(client):
    company_a, workspace_a = create_company_and_workspace(client, "A")
    _, workspace_b = create_company_and_workspace(client, "B")
    project_a = client.post(
        "/projects/start",
        json={"company_id": company_a["id"], "workspace_id": workspace_a["id"], "title": "A project"},
    ).json()
    video_b = client.post(
        f"/workspaces/{workspace_b['id']}/uploaded-videos",
        json={"platform": "youtube", "title": "B uploaded video"},
    ).json()

    response = client.post(
        f"/projects/{project_a['id']}/analytics/snapshot",
        json={"uploaded_video_id": video_b["id"], "views": 100},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "uploaded video does not belong to company/workspace"


def test_invalid_review_action_for_task_type_returns_400(client):
    company, workspace = create_company_and_workspace(client, "A")
    project = client.post(
        "/projects/start",
        json={"company_id": company["id"], "workspace_id": workspace["id"], "title": "Review"},
    ).json()
    task = client.post(
        f"/projects/{project['id']}/review-tasks",
        json={"task_type": "PRE_SPEND", "title": "Pre-spend"},
    ).json()

    response = client.post(f"/review-tasks/{task['id']}/action", json={"action": "APPROVE"})

    assert response.status_code == 400
    assert "PRE_SPEND" in response.json()["detail"]


def test_final_video_approve_transitions_to_human_approved(client):
    company, workspace = create_company_and_workspace(client, "A")
    project = client.post(
        "/projects/start",
        json={"company_id": company["id"], "workspace_id": workspace["id"], "title": "Final"},
    ).json()
    run = client.post(f"/projects/{project['id']}/run-next").json()

    response = client.post(f"/review-tasks/{run['review_task_id']}/action", json={"action": "APPROVE"})
    assert response.status_code == 200
    assert client.get(f"/projects/{project['id']}/state").json()["current_state"] == "HUMAN_APPROVED"


def test_workflow_without_preapproval_reaches_final_video_review_task(client):
    company, workspace = create_company_and_workspace(client, "A")
    project = client.post(
        "/projects/start",
        json={"company_id": company["id"], "workspace_id": workspace["id"], "title": "Normal"},
    ).json()

    run = client.post(f"/projects/{project['id']}/run-next").json()
    task = client.get(f"/review-tasks/{run['review_task_id']}").json()

    assert run["current_state"] == "WAITING_HUMAN_REVIEW"
    assert task["task_type"] == "FINAL_VIDEO"
    cost_report = client.get(f"/projects/{project['id']}/cost-report").json()
    assert cost_report["total_cost"] > 0
    assert cost_report["total_input_tokens"] > 0
    assert cost_report["total_output_tokens"] > 0
    assert "AuthorityAgent" in cost_report["cost_by_agent"]
    assert cost_report["events"][0]["media_units"] == 0.0


def test_projects_and_review_tasks_are_scoped(client):
    company_a, workspace_a = create_company_and_workspace(client, "A")
    company_b, workspace_b = create_company_and_workspace(client, "B")
    project_a = client.post(
        "/projects/start",
        json={"company_id": company_a["id"], "workspace_id": workspace_a["id"], "title": "A project"},
    ).json()
    project_b = client.post(
        "/projects/start",
        json={"company_id": company_b["id"], "workspace_id": workspace_b["id"], "title": "B project"},
    ).json()
    task_a = client.post(
        f"/projects/{project_a['id']}/review-tasks",
        json={"task_type": "FINAL_VIDEO", "title": "A task"},
    ).json()
    client.post(
        f"/projects/{project_b['id']}/review-tasks",
        json={"task_type": "FINAL_VIDEO", "title": "B task"},
    )

    assert client.get("/projects").status_code == 400
    scoped_projects = client.get(f"/projects?company_id={company_a['id']}").json()
    assert [project["id"] for project in scoped_projects] == [project_a["id"]]

    assert client.get("/review-tasks").status_code == 400
    scoped_tasks = client.get(f"/review-tasks?workspace_id={workspace_a['id']}").json()
    assert [task["id"] for task in scoped_tasks] == [task_a["id"]]


def test_phase2_constitution_memory_and_openapi_paths(client):
    company, workspace = create_company_and_workspace(client, "Phase2")

    constitution = client.post(f"/workspaces/{workspace['id']}/compile-constitution").json()
    assert constitution["active"] is True
    assert "Monetization Thesis" in constitution["content"]
    assert client.get(f"/workspaces/{workspace['id']}/constitution").json()["id"] == constitution["id"]

    bulk = client.post(
        "/memory/items/bulk",
        json={
            "embed": True,
            "items": [
                {
                    "company_id": company["id"],
                    "workspace_id": workspace["id"],
                    "scope": "workspace_only",
                    "family": "monetization_memory",
                    "title": "Buyer intent memory",
                    "content": "Affiliate CTA clicks validate buyer intent for AI creator tools.",
                }
            ],
        },
    ).json()
    assert bulk[0]["embedding"]

    search = client.post(
        "/memory/search",
        json={
            "company_id": company["id"],
            "workspace_id": workspace["id"],
            "query": "affiliate buyer intent",
            "families": ["monetization_memory"],
        },
    ).json()
    assert search[0]["id"] == bulk[0]["id"]

    pack = client.post(
        "/memory/context-pack",
        json={
            "company_id": company["id"],
            "workspace_id": workspace["id"],
            "agent_role": "AuthorityAgent",
            "query": "affiliate buyer intent",
            "families": ["monetization_memory"],
        },
    ).json()
    assert pack["memory_count"] == 1
    assert pack["items"][0]["id"] == bulk[0]["id"]

    openapi = client.get("/openapi.json").json()
    assert "/workspaces/{workspace_id}/compile-constitution" in openapi["paths"]
    assert "/memory/context-pack" in openapi["paths"]
