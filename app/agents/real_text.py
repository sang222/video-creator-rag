from __future__ import annotations

from typing import Any

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.agents.base import Agent
from app.config.settings import get_settings
from app.memory.router import MemoryRouter
from app.models.entities import AgentRun
from app.providers.factory import get_llm_provider
from app.providers.llm import LLMProvider, MockLLMProvider
from app.schemas.api import (
    AuthorityDecision,
    ComplianceChecklistResult,
    MonetizationStrategyResult,
    PublishingContentResult,
    SEOMetadataResult,
    ScriptResult,
)
from app.services.context import ContextCompilerService, WorkspaceContextService
from app.services.cost import CostTrackingService
from app.services.prompt_assembly import PromptAssemblyService
from app.services.skill_pack import SkillPackLoader


class ProviderBackedAgent(Agent):
    prompt_slug = "agent"
    output_schema: type[BaseModel] = BaseModel

    def __init__(
        self,
        *,
        llm_provider: LLMProvider | None = None,
        fallback_to_mock: bool | None = None,
        skill_loader: SkillPackLoader | None = None,
        cost_service: CostTrackingService | None = None,
    ) -> None:
        super().__init__(cost_service=cost_service)
        settings = get_settings()
        self.llm_provider = llm_provider or get_llm_provider(settings)
        self.fallback_to_mock = settings.allow_provider_fallback_to_mock if fallback_to_mock is None else fallback_to_mock
        self.skill_loader = skill_loader or SkillPackLoader()
        self.context_service = WorkspaceContextService()
        self.compiler = ContextCompilerService(self.skill_loader)
        self.memory_router = MemoryRouter()
        self.prompt_assembly_service = PromptAssemblyService()

    def __call__(
        self,
        db: Session,
        *,
        company_id: str,
        workspace_id: str,
        project_id: str | None,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        assembly = self._assemble_prompt(db, workspace_id=workspace_id, project_id=project_id, payload=payload)
        enriched = assembly.payload
        system_prompt = assembly.system_prompt
        runtime_metadata: dict[str, Any] = {}
        configured_fallback_reason = getattr(self.llm_provider, "fallback_reason", None)
        if configured_fallback_reason:
            runtime_metadata["fallback_to_mock"] = True
            runtime_metadata["fallback_reason"] = configured_fallback_reason
        try:
            response = self.llm_provider.complete_structured(
                system_prompt=system_prompt,
                payload=enriched,
                schema=self.output_schema,
            )
            output = self.output_schema.model_validate(response.output).model_dump()
        except Exception as exc:
            if not self.fallback_to_mock or isinstance(self.llm_provider, MockLLMProvider):
                raise
            fallback_reason = f"{type(exc).__name__}: {exc}"
            response = MockLLMProvider().complete_structured(
                system_prompt=system_prompt,
                payload=enriched,
                schema=self.output_schema,
            )
            output = self.output_schema.model_validate(response.output).model_dump()
            runtime_metadata.update(
                {
                    "fallback_to_mock": True,
                    "fallback_reason": fallback_reason,
                    "failed_provider": getattr(self.llm_provider, "provider_name", type(self.llm_provider).__name__),
                    "failed_model": getattr(self.llm_provider, "model", None),
                }
            )

        raw_usage_json = dict(response.raw_usage_json or {})
        raw_usage_json["prompt_assembly"] = {
            "selected_skills": assembly.selected_skills,
            "deferred_skills": assembly.deferred_skills,
            "estimated_tokens": assembly.estimated_tokens,
            "prompt_budget": assembly.prompt_budget,
            "trimmed": assembly.trimmed,
            "trim_reason": assembly.trim_reason,
        }
        if runtime_metadata:
            raw_usage_json.update(runtime_metadata)
        agent_input_json = dict(enriched)
        if runtime_metadata:
            agent_input_json["_provider_runtime"] = runtime_metadata

        db.add(
            AgentRun(
                company_id=company_id,
                workspace_id=workspace_id,
                project_id=project_id,
                agent_name=self.name,
                node_name=self.node_name,
                input_json=agent_input_json,
                output_json=output,
                status="SUCCESS",
            )
        )
        self.cost_service.record(
            db,
            company_id=company_id,
            workspace_id=workspace_id,
            project_id=project_id,
            agent_name=self.name,
            node_name=self.node_name,
            provider=response.provider,
            model=response.model,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            estimated_cost=response.estimated_cost,
            raw_usage_json=raw_usage_json,
        )
        return output

    def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError("ProviderBackedAgent requires db context; call the agent instance instead.")

    def _system_prompt(self) -> str:
        return "\n\n".join(
            [
                "You are a backend content operating-system agent. Return structured JSON only.",
                "Authority constraint: never output PUBLISH; approve, revise, reject, escalate, or pause only by the allowed enum.",
            ]
        )

    def _assemble_prompt(
        self,
        db: Session,
        *,
        workspace_id: str,
        project_id: str | None,
        payload: dict[str, Any],
    ):
        workspace_context = payload.get("workspace_context") or self.context_service.get_context(db, workspace_id)
        constitution = payload.get("compiled_workspace_operational_constitution") or self.compiler.get_active_or_compile(
            db, workspace_id
        ).content
        task_input = {
            key: value
            for key, value in payload.items()
            if key
            not in {
                "workspace_context",
                "compiled_workspace_operational_constitution",
                "memory_context_pack",
                "agent_prompt",
            }
        }
        query = self._context_query(task_input or payload)
        memory_context = payload.get("memory_context_pack") or self.memory_router.build_context_pack(
            db,
            agent_role=self.name,
            workspace_id=workspace_id,
            project_id=project_id,
            query=query,
            limit=5,
        )
        return self.prompt_assembly_service.build_agent_prompt(
            agent_role=self.prompt_slug,
            task_type=str(payload.get("task_type") or self.node_name),
            workflow_stage=str(payload.get("workflow_stage") or payload.get("gate") or self.node_name),
            base_system_prompt=self._system_prompt(),
            workspace_context=workspace_context,
            constitution=constitution,
            memory_context=memory_context,
            task_input=task_input,
            requested_skill_ids=payload.get("requested_skill_ids"),
            risk_flags=payload.get("risk_flags"),
        )

    def _context_query(self, payload: dict[str, Any]) -> str:
        evidence = payload.get("task_specific_evidence") or payload
        return " ".join(str(value) for value in evidence.values())[:500] or self.name


class AuthorityAgent(ProviderBackedAgent):
    name = "AuthorityAgent"
    node_name = "authority_gate"
    prompt_slug = "authority_agent"
    output_schema = AuthorityDecision


class ScriptAgent(ProviderBackedAgent):
    name = "ScriptAgent"
    node_name = "script"
    prompt_slug = "script_agent"
    output_schema = ScriptResult


class MonetizationStrategyAgent(ProviderBackedAgent):
    name = "MonetizationStrategyAgent"
    node_name = "monetization_strategy"
    prompt_slug = "monetization_strategy_agent"
    output_schema = MonetizationStrategyResult


class SEOMetadataAgent(ProviderBackedAgent):
    name = "SEOMetadataAgent"
    node_name = "seo_metadata"
    prompt_slug = "seo_metadata_agent"
    output_schema = SEOMetadataResult


class PublishingContentAgent(ProviderBackedAgent):
    name = "PublishingContentAgent"
    node_name = "publishing_content"
    prompt_slug = "publishing_content_agent"
    output_schema = PublishingContentResult


class ComplianceCopyrightAgent(ProviderBackedAgent):
    name = "ComplianceCopyrightAgent"
    node_name = "compliance_check"
    prompt_slug = "compliance_agent"
    output_schema = ComplianceChecklistResult
