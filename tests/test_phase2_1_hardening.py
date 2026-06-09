import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.agents import real_text
from app.config.settings import get_settings
from app.core.enums import MemoryScope, ReviewTaskType
from app.db.base import Base
from app.main import app, get_db
from app.memory.router import MemoryRouter
from app.models.entities import (
    AgentRun,
    ChannelWorkspace,
    Company,
    CostEvent,
    EditorialPlaybook,
    MemoryItem,
    VideoArtifact,
    VideoProject,
    WorkspaceBudgetPolicy,
    WorkspaceOperationalConstitution,
    WorkspaceProfile,
)
from app.providers.factory import ProviderConfigurationError, get_llm_provider
from app.providers.llm import LLMProvider, LLMProviderError, LLMResponse, MockLLMProvider, OpenAICompatibleLLMProvider
from app.services.context import ContextCompilerService
from app.services.maturity import WorkspaceMaturityService
from app.services.reviews import ReviewTaskService
from app.services.skill_pack import SkillPackLoader


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
        session = Session()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()


def create_company_workspace(db, *, company_name="Acme", workspace_name="AI Daily", platform="youtube"):
    company = Company(name=company_name)
    db.add(company)
    db.flush()
    workspace = ChannelWorkspace(
        company_id=company.id,
        workspace_name=workspace_name,
        platform=platform,
        channel_name=workspace_name,
        niche="AI creator tools",
        language="en",
        target_market=["US"],
        follower_count=100,
        published_video_count=3,
        baseline_confidence=0.05,
    )
    stage, _ = WorkspaceMaturityService().classify_workspace(workspace)
    workspace.maturity_stage = stage
    db.add(workspace)
    db.flush()
    db.add(
        WorkspaceProfile(
            workspace_id=workspace.id,
            company_id=company.id,
            brand_voice="clear and practical",
            target_audience="solo creators",
            monetization_thesis_json={"primary": "affiliate validation"},
            platform_rules_json={"copyright": "no unlicensed media"},
        )
    )
    db.add(WorkspaceBudgetPolicy(company_id=company.id, workspace_id=workspace.id))
    db.add(
        EditorialPlaybook(
            company_id=company.id,
            workspace_id=workspace.id,
            version="test_playbook_v1",
            content_json={"principles": ["reuse first"]},
            active=True,
        )
    )
    db.commit()
    return company, workspace


def add_memory(db, company, workspace, *, scope, family, title, content, platform=None):
    metadata = {"platform": platform} if platform else {}
    item = MemoryItem(
        company_id=company.id,
        workspace_id=workspace.id if scope == MemoryScope.WORKSPACE_ONLY.value else None,
        scope=scope,
        family=family,
        type="note",
        title=title,
        content=content,
        metadata_json=metadata,
        confidence=0.8,
    )
    db.add(item)
    db.commit()
    return item


def test_memory_scopes_do_not_leak_between_workspaces_companies_or_platforms(db):
    company_a, workspace_a = create_company_workspace(db, company_name="A", workspace_name="A YouTube", platform="youtube")
    _, workspace_b = create_company_workspace(db, company_name="A", workspace_name="A TikTok", platform="tiktok")
    company_c, workspace_c = create_company_workspace(db, company_name="C", workspace_name="C YouTube", platform="youtube")
    workspace_b.company_id = company_a.id
    db.commit()

    workspace_only = add_memory(
        db,
        company_a,
        workspace_a,
        scope="workspace_only",
        family="monetization_memory",
        title="Workspace A affiliate",
        content="Workspace A buyer intent.",
    )
    add_memory(
        db,
        company_a,
        workspace_b,
        scope="workspace_only",
        family="monetization_memory",
        title="Workspace B affiliate",
        content="Workspace B buyer intent.",
    )
    company_global = add_memory(
        db,
        company_a,
        workspace_a,
        scope="company_global",
        family="monetization_memory",
        title="Company A playbook",
        content="Company A monetization memory.",
    )
    add_memory(
        db,
        company_c,
        workspace_c,
        scope="company_global",
        family="monetization_memory",
        title="Company C playbook",
        content="Company C should not leak.",
    )
    platform_youtube = add_memory(
        db,
        company_a,
        workspace_a,
        scope="platform_global",
        family="monetization_memory",
        title="YouTube platform rule",
        content="YouTube platform monetization rule.",
        platform="youtube",
    )
    add_memory(
        db,
        company_a,
        workspace_a,
        scope="platform_global",
        family="monetization_memory",
        title="TikTok platform rule",
        content="TikTok should not leak into YouTube.",
        platform="tiktok",
    )
    add_memory(
        db,
        company_c,
        workspace_c,
        scope="platform_global",
        family="monetization_memory",
        title="Other company YouTube",
        content="Cross-company platform memory should not leak.",
        platform="youtube",
    )
    missing_platform = add_memory(
        db,
        company_a,
        workspace_a,
        scope="platform_global",
        family="monetization_memory",
        title="Missing platform",
        content="Ambiguous platform memory should not retrieve.",
    )

    router = MemoryRouter()
    for item in [workspace_only, company_global, platform_youtube, missing_platform]:
        router.embed_item(db, item)

    results = router.retrieve_memory(
        db,
        agent_role="AuthorityAgent",
        workspace_context={"company_id": company_a.id, "workspace_id": workspace_a.id, "platform": "youtube"},
        query="affiliate platform monetization",
        families=["monetization_memory"],
        limit=20,
    )
    ids = {item.id for item in results}

    assert workspace_only.id in ids
    assert company_global.id in ids
    assert platform_youtube.id in ids
    assert missing_platform.id not in ids
    assert all(item.company_id == company_a.id for item in results)
    assert all(item.workspace_id in {None, workspace_a.id} for item in results if item.scope == "workspace_only")
    assert all((item.metadata_json or {}).get("platform") != "tiktok" for item in results)


def test_context_pack_role_policy_and_scope_controls(db):
    company, workspace = create_company_workspace(db)
    brand = add_memory(
        db,
        company,
        workspace,
        scope="workspace_only",
        family="brand_identity_memory",
        title="Brand voice",
        content="Clear practical brand voice.",
    )
    monetization = add_memory(
        db,
        company,
        workspace,
        scope="workspace_only",
        family="monetization_memory",
        title="Affiliate revenue",
        content="Affiliate buyer intent.",
    )
    router = MemoryRouter()
    router.embed_item(db, brand)
    router.embed_item(db, monetization)

    script_pack = router.build_context_pack(
        db,
        agent_role="ScriptAgent",
        workspace_id=workspace.id,
        query="brand affiliate",
        limit=10,
    )
    assert {item["id"] for item in script_pack["items"]} == {brand.id}

    authority_pack = router.build_context_pack(
        db,
        agent_role="AuthorityAgent",
        workspace_id=workspace.id,
        query="brand affiliate",
        limit=10,
    )
    assert {brand.id, monetization.id}.issuperset({item["id"] for item in authority_pack["items"]})

    with pytest.raises(ValueError, match="not allowed"):
        router.build_context_pack(
            db,
            agent_role="ScriptAgent",
            workspace_id=workspace.id,
            query="affiliate",
            families=["monetization_memory"],
        )


def test_provider_factory_real_text_does_not_silently_mock_when_key_exists(monkeypatch):
    monkeypatch.setenv("AGENT_RUNTIME_MODE", "real_text")
    monkeypatch.setenv("USE_MOCK_PROVIDERS", "true")
    monkeypatch.setenv("ALLOW_PROVIDER_FALLBACK_TO_MOCK", "false")
    monkeypatch.setenv("LLM_PROVIDER", "openai_compatible")
    monkeypatch.setenv("LLM_BASE_URL", "https://llm.example.test/v1")
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    get_settings.cache_clear()

    provider = get_llm_provider()

    assert isinstance(provider, OpenAICompatibleLLMProvider)
    get_settings.cache_clear()


def test_provider_factory_real_text_missing_key_can_fail_actionably(monkeypatch):
    monkeypatch.setenv("AGENT_RUNTIME_MODE", "real_text")
    monkeypatch.setenv("USE_MOCK_PROVIDERS", "true")
    monkeypatch.setenv("ALLOW_PROVIDER_FALLBACK_TO_MOCK", "false")
    monkeypatch.setenv("LLM_PROVIDER", "openai_compatible")
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    get_settings.cache_clear()

    with pytest.raises(ProviderConfigurationError, match="Set LLM_BASE_URL"):
        get_llm_provider()
    get_settings.cache_clear()


def test_provider_runtime_fallback_records_reason_in_agent_run_and_cost_event(db, monkeypatch):
    class FailingProvider(LLMProvider):
        provider_name = "failing"
        model = "failing-model"

        def complete_structured(self, *, system_prompt, payload, schema):
            raise LLMProviderError("upstream validation failed")

    monkeypatch.setenv("AGENT_RUNTIME_MODE", "mock")
    get_settings.cache_clear()
    company, workspace = create_company_workspace(db)
    agent = real_text.AuthorityAgent(llm_provider=FailingProvider(), fallback_to_mock=True)

    agent(
        db,
        company_id=company.id,
        workspace_id=workspace.id,
        project_id=None,
        payload={"gate": "FINAL_EDITORIAL_GATE"},
    )
    db.commit()

    run = db.scalar(select(AgentRun).where(AgentRun.company_id == company.id))
    event = db.scalar(select(CostEvent).where(CostEvent.company_id == company.id))
    assert run.input_json["_provider_runtime"]["fallback_to_mock"] is True
    assert "upstream validation failed" in run.input_json["_provider_runtime"]["fallback_reason"]
    assert event.raw_usage_json["fallback_to_mock"] is True
    assert "upstream validation failed" in event.raw_usage_json["fallback_reason"]


def test_compliance_seo_and_publishing_contracts_include_dashboard_fields(db):
    company, workspace = create_company_workspace(db)
    compliance = real_text.ComplianceCopyrightAgent(llm_provider=MockLLMProvider())(
        db,
        company_id=company.id,
        workspace_id=workspace.id,
        project_id=None,
        payload={"render": {"assets": []}, "metadata": {"title": "Safe"}},
    )
    seo = real_text.SEOMetadataAgent(llm_provider=MockLLMProvider())(
        db,
        company_id=company.id,
        workspace_id=workspace.id,
        project_id=None,
        payload={"script": {"script": "creator workflow"}},
    )
    publishing = real_text.PublishingContentAgent(llm_provider=MockLLMProvider())(
        db,
        company_id=company.id,
        workspace_id=workspace.id,
        project_id=None,
        payload={"seo": seo},
    )

    checklist_items = {item["item"] for item in compliance["checklist"]}
    assert {
        "copyright risk",
        "reused content risk",
        "AI disclosure risk",
        "misleading claims",
        "platform safety",
        "monetization eligibility risk",
    }.issubset(checklist_items)
    assert "disclosure_required" in compliance
    assert "copyright_risk_level" in compliance
    assert seo["recommended_title"]
    assert seo["title_variants"]
    assert seo["clickbait_risk"] == "LOW"
    assert seo["misleading_risk"] == "LOW"
    assert publishing["pinned_comment"]
    assert "disclosure_note" in publishing
    assert publishing["upload_checklist"]


def test_constitution_compile_versions_are_unique_and_latest_active(db):
    _, workspace = create_company_workspace(db)
    first = ContextCompilerService().compile(db, workspace.id)
    second = ContextCompilerService().compile(db, workspace.id)

    active = list(
        db.scalars(
            select(WorkspaceOperationalConstitution).where(
                WorkspaceOperationalConstitution.workspace_id == workspace.id,
                WorkspaceOperationalConstitution.active.is_(True),
            )
        )
    )

    assert len(active) == 1
    assert active[0].id == second.id
    assert first.version != second.version
    assert second.token_estimate > 0
    assert second.source_versions["playbook"] == "test_playbook_v1"


def test_skill_pack_loader_assert_required_reports_missing_file(tmp_path):
    (tmp_path / "agents").mkdir()
    (tmp_path / "company_monetization_constitution.md").write_text("Rule #1", encoding="utf-8")
    loader = SkillPackLoader(root=tmp_path)

    with pytest.raises(FileNotFoundError, match="default_workspace_playbook.md"):
        loader.assert_required()


def create_company_and_workspace_api(client, name, platform="youtube"):
    company = client.post("/companies", json={"name": name}).json()
    workspace = client.post(
        "/workspaces",
        json={
            "company_id": company["id"],
            "workspace_name": f"{name} Workspace",
            "platform": platform,
            "channel_name": f"{name} Channel",
            "niche": "AI creator tools",
        },
    ).json()
    return company, workspace


def test_dashboard_scoped_api_contracts(client):
    company_a, workspace_a = create_company_and_workspace_api(client, "A")
    company_b, workspace_b = create_company_and_workspace_api(client, "B")
    project_a = client.post(
        "/projects/start",
        json={"company_id": company_a["id"], "workspace_id": workspace_a["id"], "title": "A project"},
    ).json()
    project_b = client.post(
        "/projects/start",
        json={"company_id": company_b["id"], "workspace_id": workspace_b["id"], "title": "B project"},
    ).json()

    assert client.get("/workspaces").status_code == 400
    scoped_workspaces = client.get(f"/workspaces?company_id={company_a['id']}").json()
    assert [workspace["id"] for workspace in scoped_workspaces] == [workspace_a["id"]]

    assert client.get(f"/projects/{project_a['id']}?company_id={company_a['id']}").status_code == 200
    assert client.get(f"/projects/{project_a['id']}?company_id={company_b['id']}").status_code == 404

    task = client.post(
        f"/projects/{project_a['id']}/review-tasks",
        json={"task_type": "FINAL_VIDEO", "title": "Final review", "payload_json": {"summary": "Review the video"}},
    ).json()
    card = client.get(
        f"/review-tasks/{task['id']}?company_id={company_a['id']}&workspace_id={workspace_a['id']}"
    ).json()
    assert card["required_actions"] == ["APPROVE", "REQUEST_CHANGES", "REJECT"]
    assert card["summary"] == "Review the video"
    assert card["project_id"] == project_a["id"]
    assert client.get(f"/review-tasks/{task['id']}?project_id={project_b['id']}").status_code == 404

    # Stable artifact shape is present even before artifact rows exist.
    artifacts = client.get(f"/projects/{project_a['id']}/artifacts?company_id={company_a['id']}").json()
    assert artifacts["project_id"] == project_a["id"]
    assert artifacts["artifacts"] == []
