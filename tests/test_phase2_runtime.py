import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.agents import real_text
from app.config.settings import get_settings
from app.core.enums import MemoryScope, ProjectState, ReviewTaskType
from app.db.base import Base
from app.memory.router import MemoryRouter
from app.models.entities import (
    AgentRun,
    ChannelWorkspace,
    Company,
    CostEvent,
    EditorialPlaybook,
    MemoryItem,
    VideoProject,
    WorkspaceBudgetPolicy,
    WorkspaceOperationalConstitution,
    WorkspaceProfile,
)
from app.providers.factory import get_llm_provider
from app.providers.llm import LLMProvider, LLMResponse, MockLLMProvider
from app.schemas.api import AuthorityDecision
from app.services.context import ContextCompilerService
from app.services.maturity import WorkspaceMaturityService
from app.services.skill_pack import REQUIRED_SKILL_PACKS, SkillPackLoader
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


def create_company_workspace(db, *, name="AI Education Daily"):
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
            default_workflow_mode="MONETIZATION_VALIDATION_MODE",
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


def test_provider_factory_falls_back_to_mock_when_keys_missing(monkeypatch):
    monkeypatch.setenv("USE_MOCK_PROVIDERS", "false")
    monkeypatch.setenv("LLM_PROVIDER", "openai_compatible")
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    get_settings.cache_clear()

    provider = get_llm_provider()

    assert isinstance(provider, MockLLMProvider)
    get_settings.cache_clear()


def test_skill_pack_loader_loads_required_prompts():
    loader = SkillPackLoader()
    loaded = loader.validate_required()

    assert all(loaded[name] for name in REQUIRED_SKILL_PACKS)
    assert "structured JSON" in loader.load_agent_prompt("authority_agent")


def test_constitution_compile_stores_single_active_version(db):
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
    assert first.id != second.id
    assert "Monetization Thesis" in second.content
    assert "Current cost/mode policy" in second.content


def test_memory_item_embed_and_search_with_mock_embedding(db):
    company, workspace = create_company_workspace(db)
    item = MemoryItem(
        company_id=company.id,
        workspace_id=workspace.id,
        scope=MemoryScope.WORKSPACE_ONLY.value,
        family="monetization_memory",
        type="note",
        title="Affiliate buyer intent",
        content="AI creator tool affiliate clicks are the main validation signal.",
    )
    db.add(item)
    db.commit()

    router = MemoryRouter()
    embedded = router.embed_item(db, item)
    results = router.retrieve_memory(
        db,
        agent_role="MonetizationStrategyAgent",
        workspace_context={"company_id": company.id, "workspace_id": workspace.id, "platform": "youtube"},
        query="affiliate clicks validation",
        families=["monetization_memory"],
        limit=3,
    )

    assert embedded.embedding
    assert results[0].id == item.id


def test_context_pack_builder_respects_workspace_isolation(db):
    company, workspace_a = create_company_workspace(db, name="Workspace A")
    workspace_b = ChannelWorkspace(
        company_id=company.id,
        workspace_name="Workspace B",
        platform="youtube",
        channel_name="Workspace B",
        niche="finance",
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
        title="AI affiliate",
        content="AI tool buyer intent belongs to workspace A.",
    )
    item_b = MemoryItem(
        company_id=company.id,
        workspace_id=workspace_b.id,
        scope=MemoryScope.WORKSPACE_ONLY.value,
        family="monetization_memory",
        type="note",
        title="Finance affiliate",
        content="Credit card buyer intent belongs only to workspace B.",
    )
    db.add_all([item_a, item_b])
    db.commit()
    router = MemoryRouter()
    router.embed_item(db, item_a)
    router.embed_item(db, item_b)

    pack = router.build_context_pack(
        db,
        agent_role="AuthorityAgent",
        workspace_id=workspace_a.id,
        query="credit card buyer intent",
        families=["monetization_memory"],
        limit=10,
    )

    assert [item["id"] for item in pack["items"]] == [item_a.id]


def test_authority_agent_structured_output_rejects_invalid_decision(db):
    class BadProvider(LLMProvider):
        provider_name = "bad"
        model = "bad-model"

        def complete_structured(self, *, system_prompt, payload, schema):
            return LLMResponse(
                output={
                    "decision": "PUBLISH",
                    "monetization_passability_impact": "POSITIVE",
                    "revenue_impact": "MEDIUM",
                    "policy_risk": "LOW",
                    "brand_fit_score": 8,
                    "audience_fit_score": 8,
                    "buyer_intent_score": 8,
                },
                provider="bad",
                model="bad-model",
            )

    company, workspace = create_company_workspace(db)
    agent = real_text.AuthorityAgent(llm_provider=BadProvider(), fallback_to_mock=False)

    with pytest.raises(ValidationError):
        agent(
            db,
            company_id=company.id,
            workspace_id=workspace.id,
            project_id=None,
            payload={"gate": "FINAL_EDITORIAL_GATE"},
        )
    with pytest.raises(ValidationError):
        AuthorityDecision.model_validate({"decision": "PUBLISH"})


def test_real_text_workflow_reaches_review_task_with_mock_provider(db):
    company, workspace = create_company_workspace(db)
    project = VideoProject(company_id=company.id, workspace_id=workspace.id, title="Real text", current_state="IDEA_FOUND")
    db.add(project)
    db.commit()
    service = Phase1WorkflowService()
    service.agent_runtime_mode = "real_text"

    task = service.run_to_review_task(db, project)

    assert task.task_type == ReviewTaskType.FINAL_VIDEO.value
    assert project.current_state == ProjectState.WAITING_HUMAN_REVIEW.value
    runs = list(db.scalars(select(AgentRun).where(AgentRun.project_id == project.id)))
    assert {"ScriptAgent", "SEOMetadataAgent", "ComplianceCopyrightAgent"}.issubset({run.agent_name for run in runs})


def test_cost_events_recorded_for_provider_backed_agent_calls(db):
    company, workspace = create_company_workspace(db)
    project = VideoProject(company_id=company.id, workspace_id=workspace.id, title="Cost", current_state="IDEA_FOUND")
    db.add(project)
    db.commit()
    service = Phase1WorkflowService()
    service.agent_runtime_mode = "real_text"

    service.run_to_review_task(db, project)

    events = list(db.scalars(select(CostEvent).where(CostEvent.project_id == project.id)))
    provider_events = [event for event in events if event.model == "mock-structured-llm"]
    assert {event.agent_name for event in provider_events} >= {
        "AuthorityAgent",
        "ScriptAgent",
        "MonetizationStrategyAgent",
        "SEOMetadataAgent",
        "PublishingContentAgent",
        "ComplianceCopyrightAgent",
    }
    assert all(event.input_tokens > 0 and event.output_tokens > 0 for event in provider_events)
    assert all(event.raw_usage_json.get("mock") is True for event in provider_events)


def test_compliance_agent_returns_structured_checklist(db):
    company, workspace = create_company_workspace(db)
    result = real_text.ComplianceCopyrightAgent(llm_provider=MockLLMProvider())(
        db,
        company_id=company.id,
        workspace_id=workspace.id,
        project_id=None,
        payload={"render": {"assets": []}, "metadata": {"title": "Safe"}},
    )

    assert result["decision"] == "PASS"
    assert result["checklist"][0]["status"] == "PASS"


def test_seo_and_publishing_agents_return_required_fields(db):
    company, workspace = create_company_workspace(db)
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

    assert seo["title"]
    assert seo["description"]
    assert seo["hashtags"]
    assert publishing["pinned_comment"]
    assert publishing["community_post"]

