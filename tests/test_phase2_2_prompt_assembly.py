import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.agents import real_text
from app.db.base import Base
from app.models.entities import AgentRun, ChannelWorkspace, Company, CostEvent, EditorialPlaybook, WorkspaceBudgetPolicy, WorkspaceProfile
from app.providers.llm import LLMProvider, LLMProviderError, MockLLMProvider
from app.services.maturity import WorkspaceMaturityService
from app.services.prompt_assembly import PromptAssemblyService
from app.services.skill_runtime import (
    PromptBudgeter,
    SkillCompressor,
    SkillDescriptor,
    SkillRegistry,
    SkillResolver,
)


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


def create_company_workspace(db):
    company = Company(name="Acme Media")
    db.add(company)
    db.flush()
    workspace = ChannelWorkspace(
        company_id=company.id,
        workspace_name="AI Education Daily",
        platform="youtube",
        channel_name="AI Education Daily",
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


def test_skill_registry_discovers_existing_markdown_skills():
    registry = SkillRegistry()
    skills = registry.discover()
    ids = {skill.skill_id for skill in skills}

    assert "company_monetization_constitution" in ids
    assert "agents_authority_agent" in ids
    assert all(skill.full_policy for skill in skills if skill.metadata["format"] == "markdown")


def test_resolver_selects_subset_and_forbidden_actions_for_role():
    registry = SkillRegistry()
    all_skills = registry.discover()
    result = SkillResolver(registry).resolve(
        agent_role="ScriptAgent",
        task_type="script",
        workflow_stage="script",
        workspace_context={"platform": "youtube"},
    )

    assert 1 <= len(result.selected_skills) < len(all_skills)
    assert "PUBLISH" in result.forbidden_actions
    assert "FINAL_APPROVAL" in result.forbidden_actions
    assert result.deferred_skills


def test_compressor_excludes_full_policy_by_default_and_respects_budget():
    skill = SkillDescriptor(
        skill_id="test_policy",
        source_path="test",
        category="common",
        priority="critical",
        token_cost_class="large",
        runtime_summary=["short safe summary"],
        hard_rules=["do useful work"],
        full_policy="SECRET_FULL_POLICY " * 200,
    )
    result = SkillCompressor().compress([skill], max_skill_tokens=20)

    assert "SECRET_FULL_POLICY" not in result.compact_context
    assert result.estimated_tokens <= 20
    assert result.trimmed is True


def test_prompt_assembly_injects_rule_and_traces_skills():
    result = PromptAssemblyService().build_agent_prompt(
        agent_role="authority_agent",
        task_type="authority_gate",
        workflow_stage="FINAL_EDITORIAL_GATE",
        base_system_prompt="Return structured JSON only.",
        workspace_context={"company_id": "c1", "workspace_id": "w1", "platform": "youtube"},
        constitution="compact constitution",
        memory_context={"items": []},
        task_input={"gate": "FINAL_EDITORIAL_GATE"},
    )

    assert "Canonical Rule #1" in result.system_prompt
    assert result.selected_skills
    assert result.deferred_skills
    assert "PUBLISH" in result.forbidden_actions
    assert result.payload["_prompt_assembly"]["selected_skills"] == result.selected_skills
    assert result.prompt_budget["skills_tokens"] > 0


def test_prompt_assembly_trims_oversized_sections():
    service = PromptAssemblyService(
        budgeter=PromptBudgeter(
            max_total_tokens=350,
            max_skill_tokens=80,
            max_memory_tokens=30,
            max_constitution_tokens=25,
            max_task_input_tokens=35,
        )
    )
    result = service.build_agent_prompt(
        agent_role="script_agent",
        task_type="script",
        workflow_stage="script",
        base_system_prompt="Return structured JSON only.",
        workspace_context={"company_id": "c1", "workspace_id": "w1", "platform": "youtube"},
        constitution="constitution " * 300,
        memory_context={"items": [{"summary": "memory " * 300}]},
        task_input={"brief": "task " * 300},
    )

    assert result.trimmed is True
    assert result.prompt_budget["trimmed"] is True
    assert result.prompt_budget["constitution_tokens"] <= 25
    assert result.prompt_budget["memory_tokens"] <= 30
    assert result.prompt_budget["task_input_tokens"] <= 35


def test_provider_backed_agent_stores_prompt_assembly_trace(db):
    company, workspace = create_company_workspace(db)
    result = real_text.AuthorityAgent(llm_provider=MockLLMProvider())(
        db,
        company_id=company.id,
        workspace_id=workspace.id,
        project_id=None,
        payload={"gate": "FINAL_EDITORIAL_GATE"},
    )
    db.commit()

    run = db.scalar(select(AgentRun).where(AgentRun.company_id == company.id))
    event = db.scalar(select(CostEvent).where(CostEvent.company_id == company.id))
    assert result["decision"] == "PASS_TO_HUMAN"
    assert "_prompt_assembly" in run.input_json
    assert run.input_json["_prompt_assembly"]["selected_skills"]
    assert "estimated_tokens" in run.input_json["_prompt_assembly"]
    assert event.raw_usage_json["prompt_assembly"]["selected_skills"]
    assert "prompt_budget" in event.raw_usage_json["prompt_assembly"]


def test_provider_fallback_observability_still_records_prompt_assembly(db):
    class FailingProvider(LLMProvider):
        provider_name = "failing"
        model = "failing-model"

        def complete_structured(self, *, system_prompt, payload, schema):
            raise LLMProviderError("upstream unavailable")

    company, workspace = create_company_workspace(db)
    real_text.AuthorityAgent(llm_provider=FailingProvider(), fallback_to_mock=True)(
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
    assert run.input_json["_prompt_assembly"]["selected_skills"]
    assert event.raw_usage_json["fallback_to_mock"] is True
    assert event.raw_usage_json["prompt_assembly"]["estimated_tokens"] > 0
