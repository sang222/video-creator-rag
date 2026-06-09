from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any

from app.services.context import MONETIZATION_RULE_1
from app.services.skill_runtime import (
    PromptBudgeter,
    SkillCompressor,
    SkillRegistry,
    SkillResolver,
    estimate_tokens,
    normalize_identifier,
)


@dataclass
class PromptAssemblyResult:
    system_prompt: str
    payload: dict[str, Any]
    selected_skills: list[str]
    deferred_skills: list[str]
    forbidden_actions: list[str]
    required_output_checks: list[str]
    prompt_budget: dict[str, Any]
    estimated_tokens: int
    trimmed: bool
    trim_reason: str | None
    assembly_trace: dict[str, Any]


class PromptAssemblyService:
    def __init__(
        self,
        *,
        registry: SkillRegistry | None = None,
        resolver: SkillResolver | None = None,
        compressor: SkillCompressor | None = None,
        budgeter: PromptBudgeter | None = None,
    ) -> None:
        self.registry = registry or SkillRegistry()
        self.resolver = resolver or SkillResolver(self.registry)
        self.compressor = compressor or SkillCompressor()
        self.budgeter = budgeter or PromptBudgeter()

    def build_agent_prompt(
        self,
        *,
        agent_role: str,
        task_type: str,
        workflow_stage: str,
        base_system_prompt: str,
        workspace_context: dict[str, Any] | None,
        constitution: str | dict[str, Any] | None,
        memory_context: dict[str, Any] | list[Any] | str | None,
        task_input: dict[str, Any] | str | None,
        requested_skill_ids: list[str] | None = None,
        risk_flags: list[str] | None = None,
    ) -> PromptAssemblyResult:
        normalized_role = normalize_identifier(agent_role)
        resolution = self.resolver.resolve(
            agent_role=normalized_role,
            task_type=task_type,
            workflow_stage=workflow_stage,
            workspace_context=workspace_context,
            risk_flags=risk_flags,
            requested_skill_ids=requested_skill_ids,
        )
        compression = self.compressor.compress(
            resolution.selected_skills,
            max_skill_tokens=self.budgeter.max_skill_tokens,
            allow_full_policy=False,
            deferred_skill_ids=resolution.deferred_ids(),
        )
        selected_skill_ids = compression.included_skill_ids
        deferred_skill_ids = sorted(set(compression.deferred_skill_ids + resolution.deferred_ids()))

        pre_budget_system = "\n\n".join(
            part
            for part in [
                base_system_prompt.strip(),
                f"Canonical Rule #1: {MONETIZATION_RULE_1}",
            ]
            if part
        )
        fitted, budget_trace = self.budgeter.fit_sections(
            system_prompt=pre_budget_system,
            skill_context=compression.compact_context,
            constitution=constitution,
            memory_context=memory_context,
            task_input=task_input,
        )
        system_prompt = "\n\n".join(
            part
            for part in [
                base_system_prompt.strip(),
                f"Canonical Rule #1: {MONETIZATION_RULE_1}",
                "Runtime skill context:",
                fitted["skill_context"],
            ]
            if part
        )

        task_payload = self._task_payload(fitted["task_input"])
        payload: dict[str, Any] = dict(task_payload)
        if workspace_context is not None:
            payload["workspace_context"] = workspace_context
        if fitted["constitution"] is not None:
            payload["compiled_workspace_operational_constitution"] = fitted["constitution"]
        if fitted["memory_context"] is not None:
            payload["memory_context_pack"] = fitted["memory_context"]
        payload.setdefault("task_specific_evidence", fitted["task_input"] if fitted["task_input"] is not None else {})
        payload["_prompt_assembly"] = {
            "agent_role": normalized_role,
            "task_type": task_type,
            "workflow_stage": workflow_stage,
            "selected_skills": selected_skill_ids,
            "deferred_skills": deferred_skill_ids,
            "forbidden_actions": resolution.forbidden_actions,
            "required_output_checks": resolution.required_output_checks,
            "estimated_tokens": budget_trace["estimated_total_tokens"],
            "prompt_budget": budget_trace,
            "trimmed": bool(budget_trace["trimmed"] or compression.trimmed),
            "trim_reason": self._join_reasons(budget_trace.get("trim_reason"), compression.trim_reason),
            "skill_trace": resolution.trace,
            "skill_context_tokens": compression.estimated_tokens,
        }
        estimated_total = estimate_tokens(system_prompt) + estimate_tokens(payload)
        assembly_trace = {
            "agent_role": normalized_role,
            "task_type": task_type,
            "workflow_stage": workflow_stage,
            "selected_skills": selected_skill_ids,
            "deferred_skills": deferred_skill_ids,
            "forbidden_actions": resolution.forbidden_actions,
            "required_output_checks": resolution.required_output_checks,
            "prompt_budget": budget_trace,
            "estimated_tokens": estimated_total,
            "trimmed": bool(budget_trace["trimmed"] or compression.trimmed),
            "trim_reason": payload["_prompt_assembly"]["trim_reason"],
        }
        payload["_prompt_assembly"]["estimated_tokens"] = estimated_total
        payload["_prompt_assembly"]["assembly_trace"] = {
            key: value for key, value in assembly_trace.items() if key != "prompt_budget"
        }
        return PromptAssemblyResult(
            system_prompt=system_prompt,
            payload=payload,
            selected_skills=selected_skill_ids,
            deferred_skills=deferred_skill_ids,
            forbidden_actions=resolution.forbidden_actions,
            required_output_checks=resolution.required_output_checks,
            prompt_budget=budget_trace,
            estimated_tokens=estimated_total,
            trimmed=assembly_trace["trimmed"],
            trim_reason=assembly_trace["trim_reason"],
            assembly_trace=assembly_trace,
        )

    def _task_payload(self, task_input: Any) -> dict[str, Any]:
        if task_input is None:
            return {}
        if isinstance(task_input, dict):
            return dict(task_input)
        if isinstance(task_input, str):
            return {"task_input": task_input}
        try:
            return {"task_input": json.dumps(task_input, ensure_ascii=True, default=str)}
        except TypeError:
            return {"task_input": str(task_input)}

    def _join_reasons(self, *reasons: str | None) -> str | None:
        clean = [reason for reason in reasons if reason]
        return "; ".join(dict.fromkeys(clean)) or None
